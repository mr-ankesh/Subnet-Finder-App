from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps
import ipaddress
import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

from config import cfg
from models import db, SpokeRequest, VnetInfo, SubnetRecord, RequestStatus
import notifications

app = Flask(__name__)
app.secret_key = cfg.SECRET_KEY

# ── Database ────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(DATA_DIR, 'requests.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "connect_args": {"timeout": 30, "check_same_thread": False},
}
db.init_app(app)

EXCEL_PATH = os.path.join(DATA_DIR, "subnets.xlsx")   # kept for one-time auto-migration only
POOLS = {"10.110": "10.110.0.0/16", "10.119": "10.119.0.0/16"}
DEFAULT_POOL = "10.110"


def _auto_migrate_excel():
    """
    One-time migration: if subnets.xlsx exists and subnet_records table is empty,
    import 'used' and 'reserved' rows from Excel into the database.
    Runs inside an app context at startup.
    """
    if not os.path.exists(EXCEL_PATH):
        return
    try:
        existing = db.session.execute(db.text("SELECT COUNT(*) FROM subnet_records")).scalar()
        if existing and existing > 0:
            log.info("[migration] subnet_records already populated (%d rows) — skipping Excel import", existing)
            return
    except Exception:
        return  # table may not exist yet; create_all handles it

    try:
        import pandas as pd
        df = pd.read_excel(EXCEL_PATH, dtype=str).fillna("")
        df.columns = [c.strip().replace(" ", "") for c in df.columns]
        if "Subnet" not in df.columns or "Status" not in df.columns:
            log.warning("[migration] subnets.xlsx missing required columns — skipping")
            return
        df["Status"] = df["Status"].str.strip().str.lower()

        from db_utils import get_pool_key
        imported = 0
        skipped = 0
        now = datetime.utcnow()
        for _, row in df.iterrows():
            subnet_str = str(row.get("Subnet", "")).strip()
            status = str(row.get("Status", "")).strip()
            if status not in ("used", "reserved") or not subnet_str:
                continue
            pool_key = get_pool_key(subnet_str)
            if not pool_key:
                skipped += 1
                continue
            allocated_at_raw = str(row.get("AllocationTime", "")).strip()
            try:
                allocated_at = datetime.strptime(allocated_at_raw[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                allocated_at = now
            record = SubnetRecord(
                subnet       = subnet_str,
                pool         = pool_key,
                status       = status,
                purpose      = str(row.get("Purpose", "")).strip() or None,
                requested_by = str(row.get("RequestedBy", "")).strip() or None,
                allocated_by = str(row.get("AllocatedBy", "")).strip() or None,
                allocated_at = allocated_at,
                created_at   = now,
                updated_at   = now,
            )
            db.session.add(record)
            imported += 1
        db.session.commit()
        log.info("[migration] Imported %d subnets from Excel (%d skipped). Excel file kept as backup.", imported, skipped)
    except Exception as exc:
        db.session.rollback()
        log.error("[migration] Excel import failed: %s", exc)


with app.app_context():
    db.create_all()
    # Migrate old request status values
    try:
        STATUS_MAP = {
            "pending": RequestStatus.CIDR_REQUESTED,
            "subnet_allocated": RequestStatus.CIDR_ASSIGNED,
            "deploying": RequestStatus.VNET_CREATED,
            "completed": RequestStatus.HUB_INTEGRATED,
            "cancelled": RequestStatus.CANCELLED,
        }
        for old, new in STATUS_MAP.items():
            db.session.execute(
                db.text("UPDATE spoke_requests SET status=:new WHERE status=:old"),
                {"new": new, "old": old}
            )
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Auto-migrate subnet inventory from Excel on first run
    _auto_migrate_excel()


# ── Admin auth ──────────────────────────────────────────────────────────────

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            # Return JSON for API routes, redirect for page routes
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("admin_login", next=request.url))
        return f(*args, **kwargs)
    return decorated


@app.context_processor
def inject_globals():
    return {"is_admin": session.get("is_admin", False), "RequestStatus": RequestStatus}


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == cfg.ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(request.form.get("next") or url_for("requests_list"))
        error = "Incorrect password."
    return render_template("admin_login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("requester_page"))


# ── Pool helpers ────────────────────────────────────────────────────────────

def get_pool_from_request():
    pool = (request.args.get("pool") or request.form.get("pool") or DEFAULT_POOL).strip()
    base_cidr = POOLS.get(pool, POOLS[DEFAULT_POOL])
    return pool, ipaddress.ip_network(base_cidr)


def compute_free_blocks(pool_key, base_net):
    """Compute free address blocks by subtracting DB-stored used/reserved subnets from base_net."""
    from db_utils import get_used_subnets_db
    used = []
    for s in get_used_subnets_db(pool_key):
        try:
            n = ipaddress.ip_network(s)
            if n.subnet_of(base_net):
                used.append(n)
        except Exception:
            continue
    used = sorted(set(used), key=lambda n: (n.prefixlen, int(n.network_address)))
    pruned = []
    for n in used:
        if any(n.subnet_of(p) for p in pruned):
            continue
        pruned.append(n)
    free = [base_net]
    for u in pruned:
        new_free = []
        for f in free:
            if not f.overlaps(u):
                new_free.append(f)
            elif f.subnet_of(u):
                continue
            elif u.subnet_of(f):
                new_free.extend(list(f.address_exclude(u)))
            else:
                new_free.append(f)
        free = new_free
    return sorted(free, key=lambda n: (n.prefixlen, int(n.network_address)))


def candidates_from_free(free_blocks, requested_prefix, limit=1024):
    out = []
    for block in free_blocks:
        if block.prefixlen < requested_prefix:
            for s in block.subnets(new_prefix=requested_prefix):
                out.append(str(s))
                if len(out) >= limit:
                    return sorted(set(out), key=lambda x: (ipaddress.ip_network(x).network_address, ipaddress.ip_network(x).prefixlen)), True
        elif block.prefixlen == requested_prefix:
            out.append(str(block))
            if len(out) >= limit:
                return sorted(set(out), key=lambda x: (ipaddress.ip_network(x).network_address, ipaddress.ip_network(x).prefixlen)), True
    return sorted(set(out), key=lambda x: (ipaddress.ip_network(x).network_address, ipaddress.ip_network(x).prefixlen)), False


def allocate_subnet(selected_cidr, base_net, pool_key, purpose="", requested_by="", allocated_by=""):
    """Validate and allocate a subnet, writing the record to the DB."""
    from db_utils import get_used_subnets_db, allocate_subnet_db
    try:
        selected_net = ipaddress.ip_network(selected_cidr, strict=False)
    except ValueError:
        return False, "Invalid subnet format"
    if not selected_net.subnet_of(base_net):
        return False, f"Selected subnet is not inside {base_net}"
    # Overlap check against existing used/reserved subnets
    for s in get_used_subnets_db(pool_key):
        try:
            if selected_net.overlaps(ipaddress.ip_network(s)):
                return False, f"Overlaps with existing subnet {s}"
        except ValueError:
            continue
    # Verify it falls within a free block
    free_blocks = compute_free_blocks(pool_key, base_net)
    if not any(selected_net.subnet_of(b) for b in free_blocks):
        return False, "Selected subnet is not part of any available block"
    return allocate_subnet_db(selected_cidr, pool_key, purpose, requested_by, allocated_by)


def deallocate_subnet(selected_cidr, base_net):
    """Remove a subnet allocation from the DB."""
    from db_utils import deallocate_subnet_db
    try:
        net = ipaddress.ip_network(selected_cidr, strict=False)
    except Exception:
        return False, "Invalid subnet format"
    if not net.subnet_of(base_net):
        return False, f"Subnet is not inside {base_net}"
    return deallocate_subnet_db(selected_cidr)


# ═══════════════════════════════════════════════════════════════════════════
# Pages
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/")
@require_admin
def segment_select():
    pools = [{"key": k, "cidr": v} for k, v in POOLS.items()]
    return render_template("index.html", pools=pools)


@app.route("/allocator/<pool_key>")
@require_admin
def allocator(pool_key):
    if pool_key not in POOLS:
        pool_key = DEFAULT_POOL
    return render_template("allocator.html", pool_key=pool_key, base_cidr=POOLS[pool_key])


# ═══════════════════════════════════════════════════════════════════════════
# Subnet APIs (unchanged)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/pool_stats")
@require_admin
def pool_stats():
    from db_utils import count_used_subnets_db
    pool, base_net = get_pool_from_request()
    used_count = count_used_subnets_db(pool)
    free_blocks = compute_free_blocks(pool, base_net)
    by_prefix = {}
    for n in free_blocks:
        by_prefix[str(n.prefixlen)] = by_prefix.get(str(n.prefixlen), 0) + 1
    return jsonify({"pool": pool, "base_cidr": str(base_net), "free_blocks": len(free_blocks), "allocated": used_count, "by_prefix": by_prefix})


@app.route("/get_subnet", methods=["POST"])
@require_admin
def get_subnet():
    pool, base_net = get_pool_from_request()
    cidr_input = request.form.get("cidr", "").strip()
    if not cidr_input.startswith("/"):
        return jsonify({"error": "Enter prefix like /24"}), 400
    try:
        requested_prefix = int(cidr_input.replace("/", ""))
    except Exception:
        return jsonify({"error": "Invalid prefix length"}), 400
    if not (8 <= requested_prefix <= 32):
        return jsonify({"error": "Prefix must be between /8 and /32"}), 400
    free_blocks = compute_free_blocks(pool, base_net)
    candidates, truncated = candidates_from_free(free_blocks, requested_prefix)
    if not candidates:
        return jsonify({"candidates": [], "message": "No available subnets found."})
    return jsonify({"candidates": candidates, "truncated": truncated, "message": "Showing top 1024." if truncated else None})


@app.route("/allocate", methods=["POST"])
@require_admin
def allocate():
    pool, base_net = get_pool_from_request()
    selected     = request.form.get("selected")
    purpose      = request.form.get("purpose", "").strip()
    requested_by = request.form.get("requested_by", "").strip()
    allocated_by = request.form.get("allocated_by", "").strip()
    if not all([selected, purpose, requested_by, allocated_by]):
        return jsonify({"error": "All fields are required"}), 400
    success, msg = allocate_subnet(selected, base_net, pool, purpose, requested_by, allocated_by)
    return jsonify({"error": msg} if not success else {"message": msg}), (400 if not success else 200)


@app.route("/deallocate", methods=["POST"])
@require_admin
def deallocate():
    pool, base_net = get_pool_from_request()
    selected = request.form.get("selected")
    if not selected:
        return jsonify({"error": "No subnet selected"}), 400
    success, msg = deallocate_subnet(selected, base_net)
    return jsonify({"error": msg} if not success else {"message": msg}), (400 if not success else 200)


@app.route("/all_available")
@require_admin
def all_available():
    pool, base_net = get_pool_from_request()
    free_blocks = compute_free_blocks(pool, base_net)
    return jsonify({"available": [{"Subnet": str(n), "Purpose": ""} for n in free_blocks]})


@app.route("/available_base")
@require_admin
def available_base_route():
    pool, base_net = get_pool_from_request()
    return jsonify({"available": [str(n) for n in compute_free_blocks(pool, base_net)]})


@app.route("/allocated")
@require_admin
def allocated():
    from db_utils import get_allocated_subnets_db
    pool, base_net = get_pool_from_request()
    rows = get_allocated_subnets_db(pool)
    if not rows:
        return jsonify({"allocated": [], "message": "No allocated subnets found"})
    result = [
        {
            "Subnet":         r["subnet"],
            "Purpose":        r["purpose"]      or "",
            "RequestedBy":    r["requested_by"] or "",
            "AllocatedBy":    r["allocated_by"] or "",
            "AllocationTime": r["allocated_at"] or "",
        }
        for r in rows
    ]
    return jsonify({"allocated": result})


@app.route("/summary_unused")
@require_admin
def summary_unused_route():
    pool, base_net = get_pool_from_request()
    free_blocks = compute_free_blocks(pool, base_net)
    by_prefix = {}
    for n in free_blocks:
        by_prefix[n.prefixlen] = by_prefix.get(n.prefixlen, 0) + 1
    return jsonify({"total_unused": len(free_blocks), "by_prefix": by_prefix})


@app.route("/free_summary")
@require_admin
def free_summary():
    pool, base_net = get_pool_from_request()
    free_blocks = compute_free_blocks(pool, base_net)
    by_prefix = {}
    for n in free_blocks:
        by_prefix[n.prefixlen] = by_prefix.get(n.prefixlen, 0) + 1
    top_n = int(request.args.get("top", "20"))
    return jsonify({"base": str(base_net), "total_free_blocks": len(free_blocks), "by_prefix": by_prefix, "top_blocks": [str(n) for n in free_blocks[:top_n]]})


# ═══════════════════════════════════════════════════════════════════════════
# Admin — Requests (protected)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/requests")
@require_admin
def requests_list():
    all_reqs = SpokeRequest.query.order_by(SpokeRequest.created_at.desc()).all()
    return render_template("requests_list.html", requests=all_reqs, RequestStatus=RequestStatus)


@app.route("/requests/<int:req_id>")
@require_admin
def request_detail(req_id):
    req = SpokeRequest.query.get_or_404(req_id)
    return render_template("request_detail.html", req=req, RequestStatus=RequestStatus)


@app.route("/requests/<int:req_id>/update-status", methods=["POST"])
@require_admin
def request_update_status(req_id):
    req = SpokeRequest.query.get_or_404(req_id)
    new_status = request.form.get("status", "").strip()
    # Admin can set these statuses from the page
    valid = [
        RequestStatus.CIDR_ASSIGNED,
        RequestStatus.HUB_INTEGRATION_IN_PROGRESS,
        RequestStatus.HUB_INTEGRATED,
        RequestStatus.CANCELLED,
    ]
    if new_status not in valid:
        return jsonify({"error": "Invalid status"}), 400

    req.status = new_status
    req.updated_at = datetime.utcnow()
    db.session.commit()

    try:
        if new_status == RequestStatus.HUB_INTEGRATION_IN_PROGRESS:
            notifications.notify_hub_in_progress(req)
        elif new_status == RequestStatus.HUB_INTEGRATED:
            notifications.notify_hub_integrated(req)
    except Exception:
        pass

    return jsonify({"message": f"Status updated to {RequestStatus.label(new_status)}", "status": new_status})


@app.route("/requests/<int:req_id>/vnet-info", methods=["GET", "POST"])
@require_admin
def request_vnet_info(req_id):
    req = SpokeRequest.query.get_or_404(req_id)
    if request.method == "POST":
        vi = req.vnet_info or VnetInfo(request_id=req.id)
        vi.subscription_id = request.form.get("subscription_id", "").strip()
        vi.vnet_id         = request.form.get("vnet_id", "").strip()
        vi.vnet_name       = request.form.get("vnet_name", "").strip()
        vi.resource_group  = request.form.get("resource_group", "").strip()
        vi.region          = request.form.get("region", "").strip()
        vi.address_space   = request.form.get("address_space", "").strip()
        vi.vpn_zpa_access  = request.form.get("vpn_zpa_access") == "yes"
        destinations = request.form.getlist("outbound_destination[]")
        ports        = request.form.getlist("outbound_port[]")
        protocols    = request.form.getlist("outbound_protocol[]")
        vi.set_outbound_rules([
            {"destination": d.strip(), "port": p.strip(), "protocol": pr.strip()}
            for d, p, pr in zip(destinations, ports, protocols) if d.strip()
        ])
        if not req.vnet_info:
            db.session.add(vi)
        db.session.commit()
        return redirect(url_for("request_detail", req_id=req.id))
    return render_template("vnet_form.html", req=req, errors=[], form={})


# ── Health check (unauthenticated, for K8s probes) ──────────────────────────

@app.route("/health")
def health():
    try:
        db.session.execute(db.text("SELECT 1"))
        count = SpokeRequest.query.count()
        return jsonify({"status": "ok", "db_path": app.config["SQLALCHEMY_DATABASE_URI"], "request_count": count}), 200
    except Exception as exc:
        return jsonify({"status": "error", "detail": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# Requester Agent (public)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/requester")
def requester_page():
    session.setdefault("requester_history", [])
    return render_template("requester.html")


@app.route("/requester/clear", methods=["POST"])
def requester_clear():
    session.pop("requester_history", None)
    return jsonify({"message": "Conversation cleared."})


# ── Form API endpoints (no agent — direct DB writes) ────────────────────────

@app.route("/api/requester/new-request", methods=["POST"])
def requester_new_request():
    from db_utils import create_spoke_request, get_spoke_request
    data = request.get_json(force=True)
    cidr_needed   = str(data.get("cidr_needed", "")).strip()
    purpose       = str(data.get("purpose", "")).strip()
    requester_name = str(data.get("requester_name", "")).strip()
    ip_range      = str(data.get("ip_range", "")).strip()
    hub_integration = bool(data.get("hub_integration", False))
    if not all([cidr_needed, purpose, requester_name, ip_range]):
        return jsonify({"error": "All fields are required."}), 400
    if ip_range not in ["10.110.0.0/16", "10.119.0.0/16"]:
        return jsonify({"error": "Invalid IP range."}), 400
    try:
        req_id = create_spoke_request(cidr_needed, purpose, requester_name, ip_range, hub_integration)
        req = get_spoke_request(req_id)
        try:
            notifications.notify_cidr_requested(req)
        except Exception:
            pass
        return jsonify({"success": True, "request_id": req_id})
    except Exception as exc:
        log.exception("Form: error creating request")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/requester/status/<int:request_id>")
def requester_get_status(request_id):
    from db_utils import get_spoke_request
    req = get_spoke_request(request_id)
    if not req:
        return jsonify({"error": f"Request #{request_id} not found."}), 404
    return jsonify(req.to_dict())


@app.route("/api/requester/vnet-created", methods=["POST"])
def requester_vnet_created():
    from db_utils import get_spoke_request, update_spoke_request
    data = request.get_json(force=True)
    request_id = data.get("request_id")
    if not request_id:
        return jsonify({"error": "Request ID is required."}), 400
    req = get_spoke_request(int(request_id))
    if not req:
        return jsonify({"error": f"Request #{request_id} not found."}), 404
    if req.status != RequestStatus.CIDR_ASSIGNED:
        return jsonify({"error": f"Status is '{req.status_label()}' — CIDR must be assigned first."}), 400
    update_spoke_request(int(request_id), status=RequestStatus.VNET_CREATED)
    req = get_spoke_request(int(request_id))
    try:
        notifications.notify_vnet_created(req)
    except Exception:
        pass
    return jsonify({"success": True, "message": f"Request #{request_id} updated to VNET Created."})


@app.route("/api/requester/reminder", methods=["POST"])
def requester_send_reminder():
    from db_utils import get_spoke_request
    data = request.get_json(force=True)
    request_id = data.get("request_id")
    message = str(data.get("message", "")).strip()
    if not request_id or not message:
        return jsonify({"error": "Request ID and message are required."}), 400
    req = get_spoke_request(int(request_id))
    if not req:
        return jsonify({"error": f"Request #{request_id} not found."}), 404
    ok = notifications.notify_reminder(req, message)
    return jsonify({"success": ok})


@app.route("/api/requester/chat", methods=["POST"])
def requester_chat():
    data = request.get_json(force=True)
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "Empty message"}), 400

    history = session.get("requester_history", [])
    history.append({"role": "user", "content": user_msg})

    reply = "Agent error."
    tool_calls = []
    try:
        import agent_requester as ag
        result = ag.chat(history)
        reply = result.get("reply", "")
        for tc in result.get("tool_calls", []):
            tool_calls.append({"tool": str(tc.get("tool", "")), "status": str(tc.get("status", ""))})
    except Exception as exc:
        log.exception("Requester agent error")
        reply = f"Agent error: {exc}"

    history.append({"role": "assistant", "content": reply})
    session["requester_history"] = history[-40:]
    session.modified = True
    return jsonify({"reply": reply, "tool_calls": tool_calls})


# ═══════════════════════════════════════════════════════════════════════════
# Admin Form API (protected, no AI)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/admin/requests")
@require_admin
def admin_list_requests_api():
    from db_utils import list_spoke_requests
    status_filter = request.args.get("status") or None
    reqs = list_spoke_requests(status_filter)
    return jsonify([r.to_dict() for r in reqs])


@app.route("/api/admin/find-subnets")
@require_admin
def admin_find_subnets_api():
    import agent_admin as ag
    pool  = request.args.get("pool", "10.110")
    prefix = request.args.get("prefix", type=int, default=24)
    result = ag._tool_find_subnets(pool=pool, prefix=prefix)
    return result, 200, {"Content-Type": "application/json"}


@app.route("/api/admin/assign-cidr", methods=["POST"])
@require_admin
def admin_assign_cidr_api():
    import agent_admin as ag
    data = request.get_json(force=True)
    result = ag._tool_assign_cidr(
        request_id=int(data.get("request_id")),
        pool=data.get("pool"),
        subnet=data.get("subnet"),
        allocated_by=data.get("allocated_by", "Admin"),
    )
    return result, 200, {"Content-Type": "application/json"}


@app.route("/api/admin/update-status", methods=["POST"])
@require_admin
def admin_update_status_api():
    import agent_admin as ag
    data = request.get_json(force=True)
    result = ag._tool_update_status(
        request_id=int(data.get("request_id")),
        status=data.get("status"),
        notes=data.get("notes"),
    )
    return result, 200, {"Content-Type": "application/json"}


@app.route("/api/admin/deallocate", methods=["POST"])
@require_admin
def admin_deallocate_api():
    import agent_admin as ag
    data = request.get_json(force=True)
    result = ag._tool_deallocate_cidr(
        request_id=int(data.get("request_id")),
        reason=data.get("reason", ""),
    )
    return result, 200, {"Content-Type": "application/json"}


# ═══════════════════════════════════════════════════════════════════════════
# Admin Agent (protected)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/agent")
@require_admin
def agent_page():
    req_id = request.args.get("req")
    req_obj = SpokeRequest.query.get(int(req_id)) if req_id and req_id.isdigit() else None
    session.setdefault("agent_history", [])
    return render_template("agent.html", request_obj=req_obj)


@app.route("/agent/clear", methods=["POST"])
@require_admin
def agent_clear():
    session.pop("agent_history", None)
    return jsonify({"message": "Conversation cleared."})


@app.route("/api/agent/chat", methods=["POST"])
@require_admin
def agent_chat():
    try:
        data = request.get_json(force=True)
        user_msg = (data.get("message") or "").strip()
        if not user_msg:
            return jsonify({"error": "Empty message"}), 400

        history = session.get("agent_history", [])
        history.append({"role": "user", "content": user_msg})

        reply = "Agent error."
        tool_calls = []
        try:
            import agent_admin as ag
            result = ag.chat(history)
            reply = result.get("reply", "")
            # Sanitise tool_calls — ensure every field is JSON-serialisable
            for tc in result.get("tool_calls", []):
                tool_calls.append({
                    "tool":   str(tc.get("tool", "")),
                    "status": str(tc.get("status", "")),
                })
        except Exception as exc:
            log.exception("Admin agent error")
            reply = f"Agent error: {exc}"

        history.append({"role": "assistant", "content": reply})
        session["agent_history"] = history[-40:]
        session.modified = True
        return jsonify({"reply": reply, "tool_calls": tool_calls})
    except Exception as exc:
        log.exception("Admin agent chat route error")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
