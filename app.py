from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps
import pandas as pd
import ipaddress
import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

from config import cfg
from models import db, SpokeRequest, VnetInfo, RequestStatus
import notifications

app = Flask(__name__)
app.secret_key = cfg.SECRET_KEY

# ── Database ────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(DATA_DIR, 'requests.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

with app.app_context():
    db.create_all()
    # Migrate old status values to new ones
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

FILE_PATH = os.path.join(DATA_DIR, "subnets.xlsx")
POOLS = {"10.110": "10.110.0.0/16", "10.119": "10.119.0.0/16"}
DEFAULT_POOL = "10.110"


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


# ── Excel helpers ───────────────────────────────────────────────────────────

def load_subnets():
    if not os.path.exists(FILE_PATH):
        return pd.DataFrame(columns=["Subnet", "Status", "Purpose", "RequestedBy", "AllocatedBy", "AllocationTime"])
    df = pd.read_excel(FILE_PATH, dtype=str).fillna("")
    df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
    df.columns = [c.strip().replace(" ", "") for c in df.columns]
    for col in ["Subnet", "Status", "Purpose", "RequestedBy", "AllocatedBy", "AllocationTime"]:
        if col not in df.columns:
            df[col] = ""
    df["Status"] = df["Status"].str.lower()
    return df


def save_subnets(df):
    df_copy = df.copy()
    df_copy.columns = ["Subnet", "Status", "Purpose", "Requested By", "Allocated By", "Allocation Time"]
    df_copy.to_excel(FILE_PATH, index=False)


# ── Pool helpers ────────────────────────────────────────────────────────────

def get_pool_from_request():
    pool = (request.args.get("pool") or request.form.get("pool") or DEFAULT_POOL).strip()
    base_cidr = POOLS.get(pool, POOLS[DEFAULT_POOL])
    return pool, ipaddress.ip_network(base_cidr)


def _in_pool(subnet_str, base_net):
    try:
        return ipaddress.ip_network(subnet_str).subnet_of(base_net)
    except Exception:
        return False


def compute_free_blocks(base_net, df):
    df_pool = df[df["Subnet"].apply(lambda s: _in_pool(s, base_net))]
    used = []
    for s in df_pool[df_pool["Status"].isin(["used", "reserved"])]["Subnet"]:
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


def allocate_subnet(selected_cidr, base_net, purpose="", requested_by="", allocated_by=""):
    df = load_subnets()
    try:
        selected_net = ipaddress.ip_network(selected_cidr)
    except ValueError:
        return False, "Invalid subnet format"
    if not selected_net.subnet_of(base_net):
        return False, f"Selected subnet is not inside {base_net}"
    df_pool = df[df["Subnet"].apply(lambda s: _in_pool(s, base_net))]
    for s in df_pool[df_pool["Status"].isin(["used", "reserved"])]["Subnet"]:
        try:
            if selected_net.overlaps(ipaddress.ip_network(s)):
                return False, f"Overlaps with existing subnet {s}"
        except ValueError:
            continue
    unused_df = df_pool[df_pool["Status"] == "unused"]
    parent_row = parent_net = None
    for idx, row in unused_df.iterrows():
        try:
            candidate = ipaddress.ip_network(row["Subnet"])
            if selected_net.subnet_of(candidate):
                parent_row, parent_net = idx, candidate
                break
        except ValueError:
            continue
    if parent_row is None:
        free_blocks = compute_free_blocks(base_net, df)
        container = next((b for b in free_blocks if selected_net.subnet_of(b)), None)
        if container is None:
            return False, "Selected subnet is not part of any available block"

        def _unused_in_container(r):
            try:
                n = ipaddress.ip_network(r["Subnet"])
                return r.get("Status", "").lower() == "unused" and n.subnet_of(container) and n.subnet_of(base_net)
            except Exception:
                return False

        df = df[~df.apply(_unused_in_container, axis=1)]
        df = pd.concat([df, pd.DataFrame([{"Subnet": str(container), "Status": "unused", "Purpose": "", "RequestedBy": "", "AllocatedBy": "", "AllocationTime": ""}])], ignore_index=True)
        df_pool = df[df["Subnet"].apply(lambda s: _in_pool(s, base_net))]
        unused_df = df_pool[df_pool["Status"] == "unused"]
        for idx, row in unused_df.iterrows():
            try:
                candidate = ipaddress.ip_network(row["Subnet"])
                if selected_net.subnet_of(candidate):
                    parent_row, parent_net = idx, candidate
                    break
            except ValueError:
                continue
        if parent_row is None:
            return False, "Internal error: could not create parent unused block"
    df = df.drop(parent_row)
    for r in list(parent_net.address_exclude(selected_net)):
        df = pd.concat([df, pd.DataFrame([{"Subnet": str(r), "Status": "unused", "Purpose": "", "RequestedBy": "", "AllocatedBy": "", "AllocationTime": ""}])], ignore_index=True)
    df = pd.concat([df, pd.DataFrame([{"Subnet": str(selected_net), "Status": "used", "Purpose": purpose, "RequestedBy": requested_by, "AllocatedBy": allocated_by, "AllocationTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}])], ignore_index=True)
    save_subnets(df)
    return True, f"Allocated {selected_cidr} successfully"


def deallocate_subnet(selected_cidr, base_net):
    df = load_subnets()
    try:
        net = ipaddress.ip_network(selected_cidr)
    except Exception:
        return False, "Invalid subnet format"
    if not net.subnet_of(base_net):
        return False, f"Subnet is not inside {base_net}"
    if selected_cidr not in df["Subnet"].tolist():
        return False, "Subnet not found"
    df.loc[df["Subnet"] == selected_cidr, ["Status", "Purpose", "RequestedBy", "AllocatedBy", "AllocationTime"]] = ["unused", "", "", "", ""]
    save_subnets(df)
    return True, f"Deallocated {selected_cidr} successfully"


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
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    df_pool = df[df["Subnet"].apply(lambda s: _in_pool(s, base_net))]
    used_count = len(df_pool[df_pool["Status"] == "used"])
    free_blocks = compute_free_blocks(base_net, df)
    by_prefix = {}
    for n in free_blocks:
        by_prefix[str(n.prefixlen)] = by_prefix.get(str(n.prefixlen), 0) + 1
    return jsonify({"pool": pool, "base_cidr": str(base_net), "free_blocks": len(free_blocks), "allocated": used_count, "by_prefix": by_prefix})


@app.route("/get_subnet", methods=["POST"])
@require_admin
def get_subnet():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    cidr_input = request.form.get("cidr", "").strip()
    if not cidr_input.startswith("/"):
        return jsonify({"error": "Enter prefix like /24"}), 400
    try:
        requested_prefix = int(cidr_input.replace("/", ""))
    except Exception:
        return jsonify({"error": "Invalid prefix length"}), 400
    if not (8 <= requested_prefix <= 32):
        return jsonify({"error": "Prefix must be between /8 and /32"}), 400
    free_blocks = compute_free_blocks(base_net, df)
    candidates, truncated = candidates_from_free(free_blocks, requested_prefix)
    if not candidates:
        return jsonify({"candidates": [], "message": "No available subnets found."})
    return jsonify({"candidates": candidates, "truncated": truncated, "message": "Showing top 1024." if truncated else None})


@app.route("/allocate", methods=["POST"])
@require_admin
def allocate():
    pool, base_net = get_pool_from_request()
    selected    = request.form.get("selected")
    purpose     = request.form.get("purpose", "").strip()
    requested_by = request.form.get("requested_by", "").strip()
    allocated_by = request.form.get("allocated_by", "").strip()
    if not all([selected, purpose, requested_by, allocated_by]):
        return jsonify({"error": "All fields are required"}), 400
    success, msg = allocate_subnet(selected, base_net, purpose, requested_by, allocated_by)
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
    df = load_subnets()
    free_blocks = compute_free_blocks(base_net, df)
    return jsonify({"available": [{"Subnet": str(n), "Purpose": ""} for n in free_blocks]})


@app.route("/available_base")
@require_admin
def available_base_route():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    return jsonify({"available": [str(n) for n in compute_free_blocks(base_net, df)]})


@app.route("/allocated")
@require_admin
def allocated():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    df_pool = df[df["Subnet"].apply(lambda s: _in_pool(s, base_net))]
    used = df_pool[df_pool["Status"] == "used"]
    if used.empty:
        return jsonify({"allocated": [], "message": "No allocated subnets found"})
    return jsonify({"allocated": used[["Subnet", "Purpose", "RequestedBy", "AllocatedBy", "AllocationTime"]].to_dict(orient="records")})


@app.route("/summary_unused")
@require_admin
def summary_unused_route():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    free_blocks = compute_free_blocks(base_net, df)
    by_prefix = {}
    for n in free_blocks:
        by_prefix[n.prefixlen] = by_prefix.get(n.prefixlen, 0) + 1
    return jsonify({"total_unused": len(free_blocks), "by_prefix": by_prefix})


@app.route("/free_summary")
@require_admin
def free_summary():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    free_blocks = compute_free_blocks(base_net, df)
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


@app.route("/api/requester/chat", methods=["POST"])
def requester_chat():
    data = request.get_json(force=True)
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "Empty message"}), 400

    history = session.get("requester_history", [])
    history.append({"role": "user", "content": user_msg})

    result = {"reply": "Agent error.", "tool_calls": []}
    try:
        import agent_requester as ag
        result = ag.chat(history)
    except Exception as exc:
        log.exception("Requester agent error")
        result["reply"] = f"Agent error: {exc}"

    history.append({"role": "assistant", "content": result["reply"]})
    session["requester_history"] = history[-40:]
    session.modified = True
    return jsonify({"reply": result["reply"], "tool_calls": result.get("tool_calls", [])})


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
    data = request.get_json(force=True)
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "Empty message"}), 400

    history = session.get("agent_history", [])
    history.append({"role": "user", "content": user_msg})

    result = {"reply": "Agent error.", "tool_calls": []}
    try:
        import agent_admin as ag
        result = ag.chat(history)
    except Exception as exc:
        log.exception("Admin agent error")
        result["reply"] = f"Agent error: {exc}"

    history.append({"role": "assistant", "content": result["reply"]})
    session["agent_history"] = history[-40:]
    session.modified = True
    return jsonify({"reply": result["reply"], "tool_calls": result.get("tool_calls", [])})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
