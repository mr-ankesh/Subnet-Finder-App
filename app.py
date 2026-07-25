from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps
import ipaddress
import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

import audit
import auth_oidc as oidc
import changes
import search
import settings_store
from config import (cfg, CATEGORIES, SETTINGS_SPEC, resolve, settings_view,
                    AKS_FALLBACK_VERSIONS, AKS_FALLBACK_SIZES, AKS_STANDARD_REGION)
from models import db, SpokeRequest, VnetInfo, SubnetRecord, RequestStatus, RequestType, FwCollection
from naming import render_name
import notifications

app = Flask(__name__)
app.secret_key = cfg.SECRET_KEY

# Behind the K8s ingress / reverse proxy: honour X-Forwarded-* so client IPs
# in the audit trail and https redirect URIs are correct.
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Set SESSION_COOKIE_SECURE=true in production (TLS via ingress)
app.config["SESSION_COOKIE_SECURE"] = os.environ.get(
    "SESSION_COOKIE_SECURE", "").lower() in ("1", "true", "yes")

# ── Database ────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# DATABASE_URL (Postgres) → multi-replica capable; unset → bundled SQLite file.
import db_backend
app.config["SQLALCHEMY_DATABASE_URI"] = db_backend.sqlalchemy_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
if db_backend.IS_POSTGRES:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True, "pool_recycle": 1800,
    }
else:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"timeout": 30, "check_same_thread": False},
    }
db.init_app(app)

class _SkipMigration(Exception):
    """Sentinel to short-circuit the SQLite-only column backfill on Postgres."""


EXCEL_PATH = os.path.join(DATA_DIR, "subnets.xlsx")   # kept for one-time auto-migration only
POOLS = {"10.110": "10.110.0.0/16", "10.119": "10.119.0.0/16"}
DEFAULT_POOL = "10.110"


def _auto_migrate_excel():
    """
    One-time migration: if subnets.xlsx exists and subnet_records table is empty,
    import 'used' and 'reserved' rows from Excel into the database.
    Runs inside an app context at startup.
    """
    if os.environ.get("SKIP_BOOTSTRAP_MIGRATION") or db_backend.IS_POSTGRES:
        # Postgres deployments import inventory via /admin/inventory; never
        # auto-seed from a stray xlsx (and the SQLite→PG migration disables it).
        return
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
        duplicates = 0
        seen = set()   # guard against duplicate subnets within the Excel file itself
        now = datetime.utcnow()
        for _, row in df.iterrows():
            subnet_str = str(row.get("Subnet", "")).strip()
            status = str(row.get("Status", "")).strip()
            if status not in ("used", "reserved") or not subnet_str:
                continue
            # Normalise CIDR so "10.119.100.0/22" and "10.119.100.0/22 " are the same key
            try:
                subnet_str = str(ipaddress.ip_network(subnet_str, strict=False))
            except Exception:
                skipped += 1
                continue
            if subnet_str in seen:
                log.warning("[migration] Duplicate subnet in Excel, skipping: %s", subnet_str)
                duplicates += 1
                continue
            seen.add(subnet_str)
            pool_key = get_pool_key(subnet_str)
            if not pool_key:
                skipped += 1
                continue
            # Skip rows already in the DB (handles partial previous runs)
            existing_row = SubnetRecord.query.filter_by(subnet=subnet_str).first()
            if existing_row:
                duplicates += 1
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
        log.info("[migration] Imported %d subnets from Excel (%d skipped, %d duplicates ignored). Excel file kept as backup.", imported, skipped, duplicates)
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
            # Retired status — fold back into VNET_CREATED
            "HUB_INTEGRATION_NEEDED": RequestStatus.VNET_CREATED,
        }
        for old, new in STATUS_MAP.items():
            db.session.execute(
                db.text("UPDATE spoke_requests SET status=:new WHERE status=:old"),
                {"new": new, "old": old}
            )
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Lightweight schema migration for older SQLite files (adds columns that
    # predate later features). On Postgres, create_all() already builds the
    # full current schema, and PRAGMA table_info is SQLite-only — so skip it.
    try:
        if db_backend.IS_POSTGRES:
            cols = vcols = []
            raise _SkipMigration
        cols = [r[1] for r in db.session.execute(db.text("PRAGMA table_info(spoke_requests)"))]
        if "requester_email" not in cols:
            db.session.execute(db.text("ALTER TABLE spoke_requests ADD COLUMN requester_email VARCHAR(200)"))
            db.session.commit()
            log.info("[migration] added spoke_requests.requester_email column")
        if "deployment_mode" not in cols:
            db.session.execute(db.text("ALTER TABLE spoke_requests ADD COLUMN deployment_mode VARCHAR(10) NOT NULL DEFAULT 'self'"))
            db.session.commit()
            log.info("[migration] added spoke_requests.deployment_mode column")
        # Multi-type requests: request_type + details JSON
        if "request_type" not in cols:
            db.session.execute(db.text(
                "ALTER TABLE spoke_requests ADD COLUMN request_type VARCHAR(30) NOT NULL DEFAULT 'vnet_new'"))
            db.session.commit()
            log.info("[migration] added spoke_requests.request_type column")
        if "details" not in cols:
            db.session.execute(db.text("ALTER TABLE spoke_requests ADD COLUMN details TEXT"))
            db.session.commit()
            log.info("[migration] added spoke_requests.details column")
        # vnet_info: subnet detail columns
        vcols = [r[1] for r in db.session.execute(db.text("PRAGMA table_info(vnet_info)"))]
        for col, ddl in (("subnet_name", "VARCHAR(120)"), ("subnet_size", "VARCHAR(10)"),
                         ("subnet_purpose", "VARCHAR(200)")):
            if col not in vcols:
                db.session.execute(db.text(f"ALTER TABLE vnet_info ADD COLUMN {col} {ddl}"))
        db.session.commit()
    except _SkipMigration:
        db.session.rollback()
    except Exception:
        db.session.rollback()

    # Auto-migrate subnet inventory from Excel on first run
    _auto_migrate_excel()

    # Settings overrides table (admin settings UI)
    settings_store.ensure_table()

    # Audit trail table
    audit.ensure_table()

    # Change ledger (undo history)
    changes.ensure_table()

    # Persistent agent chats
    import chats
    chats.ensure_table()

    # Subscription inventory (owner/budget metadata)
    import subinventory
    subinventory.ensure_table()

# Keycloak OIDC — registers the app; the client is built lazily from live
# settings, so enabling/configuring SSO in the portal needs no restart.
oidc.init_oidc(app)


# ── Admin auth ──────────────────────────────────────────────────────────────

def _login_endpoint():
    """Where unauthenticated users are sent — Keycloak when enabled, else the
    local password form."""
    return "auth_login" if oidc.enabled() else "admin_login"


def _home_endpoint() -> str:
    """The landing page for the signed-in user, by highest capability."""
    if session.get("is_admin"):
        return "requests_list"
    if session.get("is_allocator"):
        return "segment_select"
    if session.get("is_itadmin"):
        return "it_reachability"
    if session.get("is_requester"):
        return "requester_page"
    return "requester_page"


def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            # Already signed in but NOT an admin (a requester or allocator who
            # hit an admin URL): send them to their own home — never back to
            # login, or SSO bounces forever (redirect loop).
            if session.get("is_requester") or session.get("is_allocator") or session.get("sso"):
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Admin access required"}), 403
                return redirect(url_for(_home_endpoint()))
            # Not authenticated at all → login.
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for(_login_endpoint(), next=request.url))
        return f(*args, **kwargs)
    return decorated


def require_subnet_access(f):
    """Guards the subnet allocator — admins OR subnet-allocators. Open when SSO
    is off (legacy). A signed-in user without either role is sent to their home."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("is_admin") or session.get("is_allocator") or not session.get("sso"):
            return f(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Subnet-allocator access required"}), 403
        return redirect(url_for(_home_endpoint()))
    return decorated


def require_itadmin(f):
    """Guards the Reachability Tester — IT-admins OR super-admins. Open when SSO
    is off (legacy). A signed-in user without access is sent to their home."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("is_itadmin") or session.get("is_superadmin") or not session.get("sso"):
            return f(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"error": "IT-admin access required"}), 403
        return redirect(url_for(_home_endpoint()))
    return decorated


def require_superadmin(f):
    """Guards Settings and the Audit trail — super-admins only. A signed-in
    admin without the super-admin role gets 403 (not a login bounce)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("is_superadmin"):
            return f(*args, **kwargs)
        if session.get("is_admin") or session.get("is_requester") or session.get("sso"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Super-admin access required"}), 403
            return render_template("forbidden.html",
                                   need="Super Admin",
                                   detail="Settings and the Audit trail are restricted to "
                                          "super-admins."), 403
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401
        return redirect(url_for(_login_endpoint(), next=request.url))
    return decorated


def require_login(f):
    """Requester-portal guard. Open when SSO is off (legacy behaviour); when
    Keycloak is on, requires an authenticated requester (or admin)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if oidc.enabled() and not (session.get("is_requester") or session.get("is_admin")):
            # Signed in but without portal access (e.g. a pure subnet-allocator)
            # → their own home, not a login bounce. Truly anonymous → login.
            if session.get("sso"):
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Requester access required"}), 403
                return redirect(url_for(_home_endpoint()))
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("auth_login", next=request.url))
        return f(*args, **kwargs)
    return decorated


@app.context_processor
def inject_globals():
    return {"is_admin": session.get("is_admin", False), "RequestStatus": RequestStatus,
            "RequestType": RequestType, "AZURE_DRY_RUN": cfg.AZURE_DRY_RUN,
            "sso_enabled": oidc.enabled(),
            "sso": bool(session.get("sso")),
            "sso_user": session.get("sso_user"),
            "sso_email": session.get("sso_email"),
            "sso_name": session.get("admin_name") if session.get("sso") else None,
            # Only lock a form field when Keycloak actually supplied that value.
            "sso_lock_name": bool(session.get("sso") and session.get("sso_has_name")),
            "sso_lock_email": bool(session.get("sso") and session.get("sso_email")),
            "is_superadmin": session.get("is_superadmin", False),
            "is_allocator": session.get("is_allocator", False),
            "is_itadmin": session.get("is_itadmin", False),
            "is_requester": session.get("is_requester", False),
            "is_authed": bool(session.get("is_admin") or session.get("is_requester")
                              or session.get("is_allocator") or session.get("is_itadmin")),
            # AKS request form: version/size choices + the defaults applied for
            # everything the requester doesn't pick (shown as a read-only note).
            "aks_k8s_options": list(AKS_FALLBACK_VERSIONS),
            "aks_node_sizes":  list(AKS_FALLBACK_SIZES),
            "aks_default_region": AKS_STANDARD_REGION,
            "aks_defaults": {"node_count": cfg.AKS_DEFAULT_NODE_COUNT,
                             "min": cfg.AKS_DEFAULT_MIN_COUNT, "max": cfg.AKS_DEFAULT_MAX_COUNT,
                             "tier": cfg.AKS_DEFAULT_TIER, "region": AKS_STANDARD_REGION,
                             "zones": cfg.AKS_DEFAULT_ZONES,
                             "plugin": cfg.AKS_NETWORK_PLUGIN, "plugin_mode": cfg.AKS_NETWORK_PLUGIN_MODE,
                             "policy": cfg.AKS_NETWORK_POLICY, "pod_cidr": cfg.AKS_POD_CIDR,
                             "service_cidr": cfg.AKS_SERVICE_CIDR, "dns_ip": cfg.AKS_DNS_SERVICE_IP,
                             "private": cfg.AKS_PRIVATE_CLUSTER, "outbound": cfg.AKS_OUTBOUND_TYPE,
                             "upgrade": cfg.AKS_UPGRADE_CHANNEL, "node_os": cfg.AKS_NODE_OS_UPGRADE_CHANNEL}}


def current_actor() -> str:
    """Display name for the audit trail: admin's login name, or 'Admin'."""
    return session.get("admin_name") or "Admin"


def _chat_owner(kind: str) -> str:
    """Stable per-user key that owns persistent agent chats. Uses the Keycloak
    identity when signed in; otherwise the admin name, or a per-session id for an
    anonymous requester."""
    if session.get("sso"):
        return (session.get("sso_email") or session.get("sso_user")
                or session.get("admin_name") or "sso-user")
    if kind == "admin":
        return session.get("admin_name") or "admin"
    uid = session.get("chat_uid")
    if not uid:
        import uuid as _uuid
        uid = "anon-" + _uuid.uuid4().hex[:12]
        session["chat_uid"] = uid
    return uid


def _sso_identity(client_name: str = "", client_email: str = ""):
    """
    Effective (name, email) for a request. When signed in via Keycloak, its
    values win — but if Keycloak lacks a name/email, keep what the user typed.
    """
    if session.get("sso"):
        name = session.get("admin_name") if session.get("sso_has_name") else (client_name or session.get("admin_name") or "")
        email = session.get("sso_email") or client_email or ""
        return (name or client_name or "user"), (email or None)
    return client_name, (client_email or None)


def _requester_owner():
    """(name, email) identifying the signed-in requester's requests, or None
    when SSO is off (open mode — no per-user ownership)."""
    if session.get("sso"):
        return session.get("admin_name"), (session.get("sso_email") or None)
    return None, None


def _owns_request(req) -> bool:
    """True if the current user may view this request. Admins: all. SSO
    requesters: only theirs. Open mode (no SSO): unrestricted (legacy)."""
    if session.get("is_admin"):
        return True
    if not session.get("sso"):
        return True
    name, email = _requester_owner()
    rmail = (getattr(req, "requester_email", None) or "").lower()
    if email and rmail == email.lower():
        return True
    if not email and name and (getattr(req, "requester_name", None) or "") == name:
        return True
    return False


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    # When Keycloak is on, a plain GET goes to SSO. '?local=1' is the
    # break-glass path that still shows the password form (SSO outage / setup).
    if request.method == "GET" and oidc.enabled() and request.args.get("local") != "1":
        return redirect(url_for("auth_login", next=request.args.get("next")))
    error = None
    if request.method == "POST":
        name = (request.form.get("admin_name") or "").strip()[:100]
        if request.form.get("password") == cfg.ADMIN_PASSWORD:
            # The local break-glass account is the master account → super-admin
            # (full portal access, so it can reach Settings during an SSO outage).
            session["is_admin"] = True
            session["is_superadmin"] = True
            session["admin_name"] = name or "Admin"
            audit.record("admin_login", actor=session["admin_name"], actor_role="admin",
                         summary=f"Local break-glass (super-admin) login from {request.remote_addr}")
            return redirect(request.form.get("next") or url_for("requests_list"))
        error = "Incorrect password."
        audit.record("admin_login_failed", actor=name or "unknown", actor_role="system",
                     summary=f"Failed local admin login from {request.remote_addr}")
    return render_template("admin_login.html", error=error, break_glass=oidc.enabled())


@app.route("/admin/logout")
def admin_logout():
    was_sso = bool(session.get("sso"))
    session.clear()
    if was_sso and oidc.enabled():
        return redirect(url_for("auth_logout"))
    return redirect(url_for("requester_page"))


# ── Keycloak OIDC flow ──────────────────────────────────────────────────────

@app.route("/auth/login")
def auth_login():
    if not oidc.enabled():
        return redirect(url_for("admin_login", local=1))
    if "next" in request.args:
        session["auth_next"] = request.args.get("next") or ""
    try:
        return oidc.client().authorize_redirect(url_for("auth_callback", _external=True))
    except Exception as exc:
        log.error("OIDC redirect failed: %s", exc)
        return render_template("admin_login.html",
                               error="Could not reach the SSO provider. Use local login if you are an admin.",
                               break_glass=True), 502


@app.route("/auth/callback")
def auth_callback():
    if not oidc.enabled():
        return redirect(url_for("admin_login"))
    try:
        token = oidc.client().authorize_access_token()
    except Exception as exc:
        # Show the error page (NEVER auto-redirect back to login — that turns a
        # session/state hiccup into an infinite redirect loop).
        log.error("OIDC callback failed: %s", exc)
        audit.record("sso_login_failed", actor="unknown", actor_role="system",
                     summary=f"SSO sign-in failed from {request.remote_addr}: {str(exc)[:150]}")
        return render_template("admin_login.html",
                               error="SSO sign-in failed — please try signing in again.",
                               break_glass=True), 400

    info = token.get("userinfo") or {}
    roles = oidc.roles_from_token(token)
    is_superadmin = bool(cfg.KEYCLOAK_SUPERADMIN_ROLE) and cfg.KEYCLOAK_SUPERADMIN_ROLE in roles
    is_admin = is_superadmin or (cfg.KEYCLOAK_ADMIN_ROLE in roles)
    is_allocator = is_admin or (bool(cfg.KEYCLOAK_ALLOCATOR_ROLE) and cfg.KEYCLOAK_ALLOCATOR_ROLE in roles)
    is_itadmin = is_superadmin or (bool(cfg.KEYCLOAK_ITADMIN_ROLE) and cfg.KEYCLOAK_ITADMIN_ROLE in roles)
    req_role = cfg.KEYCLOAK_REQUESTER_ROLE
    is_requester = is_admin or (not req_role) or (req_role in roles)

    username = info.get("preferred_username") or info.get("email") or "user"
    # Name from Keycloak: 'name', else given+family, else the username.
    full = (((info.get("given_name") or "") + " " + (info.get("family_name") or "")).strip())
    display = info.get("name") or full or username

    if not (is_admin or is_requester or is_allocator or is_itadmin):
        audit.record("sso_denied", actor=display, actor_role="system",
                     summary=f"SSO login denied for {username} — no recognised role")
        return render_template("sso_denied.html", username=username,
                               admin_role=cfg.KEYCLOAK_ADMIN_ROLE,
                               requester_role=req_role), 403

    session["sso"] = True
    session["is_admin"] = is_admin
    session["is_superadmin"] = is_superadmin
    session["is_allocator"] = is_allocator
    session["is_itadmin"] = is_itadmin
    session["is_requester"] = is_requester
    session["sso_user"] = username
    session["sso_email"] = info.get("email") or ""
    session["sso_has_name"] = bool(info.get("name") or full)   # real name vs username fallback
    session["admin_name"] = display               # audit actor
    if token.get("id_token"):
        session["sso_id_token"] = token["id_token"]

    granted = [r for r, ok in (("super-admin", is_superadmin),
                               ("admin", is_admin and not is_superadmin),
                               ("allocator", is_allocator and not is_admin),
                               ("it-admin", is_itadmin and not is_superadmin),
                               ("requester", is_requester and not is_admin)) if ok]
    audit.record("admin_login" if is_admin else "sso_login",
                 actor=display, actor_role="admin" if is_admin else "requester",
                 summary=f"Keycloak SSO login ({username}, roles: {', '.join(granted) or 'none'})")
    # Land on the highest-capability home (session is set → _home_endpoint is
    # the single source of truth; the nav offers the other areas they can reach).
    dest = session.pop("auth_next", "") or url_for(_home_endpoint())
    return redirect(dest)


@app.route("/auth/logout")
def auth_logout():
    id_token = session.get("sso_id_token")
    session.clear()
    if oidc.enabled():
        post = url_for("requester_page", _external=True)
        return redirect(oidc.end_session_url(post, id_token))
    return redirect(url_for("requester_page"))


# ── Admin settings (DB-backed config overrides) ─────────────────────────────

def _validate_setting(key: str, spec: dict, value: str):
    """Return an error string, or None if the value is acceptable."""
    if spec["options"] and value not in spec["options"]:
        return f"Must be one of: {', '.join(spec['options'])}."
    if spec["type"] == "bool" and value.lower() not in ("true", "false", "1", "0", "yes", "no"):
        return "Must be true or false."
    if spec["type"] == "int":
        if not value.isdigit():
            return "Must be a whole number."
    if key == "HUB_FIREWALL_PRIVATE_IP" and value:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return "Must be a valid IP address."
    if key.startswith("TPL_") and not value:
        return "Naming template cannot be empty."
    return None


@app.route("/admin/settings")
@require_superadmin
def admin_settings():
    return render_template(
        "settings.html",
        categories=settings_view(),
        weak_secret_key=settings_store.using_default_secret_key(),
    )


@app.route("/api/admin/settings", methods=["POST"])
@require_superadmin
def admin_settings_save():
    data = request.get_json(force=True) or {}
    to_save, errors = [], {}
    for key, value in data.items():
        spec = SETTINGS_SPEC.get(key)
        if spec is None:
            errors[key] = "Unknown setting."
            continue
        value = ("" if value is None else str(value)).strip()
        if spec["secret"] and value == "":
            continue                     # blank secret input = keep current value
        err = _validate_setting(key, spec, value)
        if err:
            errors[key] = err
            continue
        current, _source = resolve(key)
        if value == str(current):
            continue                     # unchanged — don't create a needless override
        to_save.append((key, value, spec["secret"]))

    # Atomic-ish: apply nothing if any field failed validation
    saved = []
    if not errors:
        for key, value, is_secret in to_save:
            settings_store.set_override(key, value, is_secret=is_secret)
            saved.append(key)
    if saved:
        log.info("[settings] admin updated: %s", ", ".join(saved))
        # Keys only — secret values must never reach the audit trail
        audit.record("settings_changed", actor=current_actor(), actor_role="admin",
                     summary=f"Settings updated: {', '.join(saved)}", data={"keys": saved})
    return jsonify({"success": not errors, "saved": saved, "errors": errors}), (200 if not errors else 400)


@app.route("/api/admin/settings/reset", methods=["POST"])
@require_superadmin
def admin_settings_reset():
    key = (request.get_json(force=True) or {}).get("key", "")
    if key not in SETTINGS_SPEC:
        return jsonify({"success": False, "error": "Unknown setting."}), 400
    settings_store.delete_override(key)
    log.info("[settings] admin reset override: %s", key)
    audit.record("settings_reset", actor=current_actor(), actor_role="admin",
                 summary=f"Setting reverted to env/default: {key}", data={"key": key})
    raw, source = resolve(key)
    spec = SETTINGS_SPEC[key]
    value = "" if spec["secret"] else raw
    return jsonify({"success": True, "key": key, "value": value, "source": source,
                    "is_set": bool(raw) if spec["secret"] else None})


# ── Firewall collection definitions (one-time admin setup) ─────────────────

@app.route("/api/admin/firewall/collections", methods=["GET", "POST"])
@require_admin
def fw_collections():
    if request.method == "POST":
        # Defining collections is a Settings-tab action → super-admin only.
        # (GET stays open to admins — it feeds the request-processing dropdowns.)
        if not session.get("is_superadmin"):
            return jsonify({"error": "Super-admin access required"}), 403
        data = request.get_json(force=True) or {}
        rcg = str(data.get("rcg", "")).strip()
        col = str(data.get("collection", "")).strip()
        desc = str(data.get("description", "")).strip()[:300]
        act = str(data.get("action", "Allow")).strip().title()
        try:
            prio = int(data.get("priority", 200))
        except (TypeError, ValueError):
            prio = 0
        if not rcg or not col:
            return jsonify({"error": "Rule collection group and rule collection names are required."}), 400
        if act not in ("Allow", "Deny"):
            return jsonify({"error": "Action must be Allow or Deny."}), 400
        if not (100 <= prio <= 65000):
            return jsonify({"error": "Priority must be between 100 and 65000."}), 400
        if FwCollection.query.filter_by(rcg=rcg, collection=col).first():
            return jsonify({"error": f"'{rcg} / {col}' is already defined."}), 400
        row = FwCollection(rcg=rcg, collection=col, priority=prio, action=act,
                           description=desc or None)
        db.session.add(row)
        db.session.commit()
        audit.record("fw_collection_defined", actor=current_actor(), actor_role="admin",
                     summary=f"Firewall collection defined: {rcg} / {col} ({act}, prio {prio})",
                     data=row.to_dict())
        return jsonify({"success": True, "item": row.to_dict()})

    include_azure = request.args.get("azure") == "1"
    defined = [f.to_dict() for f in
               FwCollection.query.order_by(FwCollection.rcg, FwCollection.priority).all()]
    azure_list, azure_error = [], None
    if include_azure:
        import azure_tools
        st = azure_tools.get_firewall_policy_status()
        if st.get("success") and st.get("policy_exists"):
            azure_list = [{"rcg": c["rcg"], "collection": c["name"], "priority": c["priority"],
                           "action": c["action"], "filter": c["filter"],
                           "rule_type": c.get("rule_type", ""),
                           "rules": len(c["rules"])} for c in st["collections"]]
        else:
            azure_error = st.get("message")
    return jsonify({"defined": defined, "azure": azure_list, "azure_error": azure_error})


@app.route("/api/admin/firewall/collections/<int:cid>/delete", methods=["POST"])
@require_superadmin
def fw_collections_delete(cid):
    row = FwCollection.query.get_or_404(cid)
    info = row.to_dict()
    db.session.delete(row)
    db.session.commit()
    audit.record("fw_collection_removed", actor=current_actor(), actor_role="admin",
                 summary=f"Firewall collection definition removed: {info['rcg']} / {info['collection']}",
                 data=info)
    return jsonify({"success": True})


@app.route("/api/admin/settings/test-azure", methods=["POST"])
@require_superadmin
def admin_settings_test_azure():
    import azure_tools
    res = azure_tools.test_connection()
    return jsonify(res), (200 if res.get("success") else 400)


@app.route("/api/admin/settings/test-connector", methods=["POST"])
@require_superadmin
def admin_settings_test_connector():
    import reachability
    src = str((request.get_json(force=True) or {}).get("source", "")).strip()
    res = reachability.test_ssh(src)
    return jsonify(res), (200 if res.get("success") else 400)


@app.route("/api/admin/settings/test-keycloak", methods=["POST"])
@require_superadmin
def admin_settings_test_keycloak():
    # Rebuild the OIDC client so the test reflects just-saved settings.
    oidc._oauth = None
    oidc._fingerprint = None
    res = oidc.test_connection()
    return jsonify(res), (200 if res.get("success") else 400)


@app.route("/api/admin/settings/preview-name", methods=["POST"])
@require_superadmin
def admin_settings_preview_name():
    """Live preview for the naming tab — renders a template with sample values."""
    data = request.get_json(force=True) or {}
    key = data.get("key", "")
    if key not in SETTINGS_SPEC or not key.startswith("TPL_"):
        return jsonify({"error": "Unknown template."}), 400
    preview = render_name(
        key, vnet="my-spoke-vnet", request_id=42, region=cfg.DEFAULT_AZURE_REGION,
        cidr_mask=24, purpose="analytics platform",
        template_override=str(data.get("template", "")).strip() or None,
        prefix_override=str(data.get("prefix", "")) if "prefix" in data else None,
        suffix_override=str(data.get("suffix", "")) if "suffix" in data else None,
    )
    return jsonify({"preview": preview})


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
@require_subnet_access
def segment_select():
    pools = [{"key": k, "cidr": v} for k, v in POOLS.items()]
    inventory_empty = SubnetRecord.query.count() == 0
    return render_template("index.html", pools=pools, inventory_empty=inventory_empty)


# ── Subnet inventory import (fresh deployments start with an empty DB) ──────

def _import_inventory(rows):
    """
    Bulk-load current allocations. rows = [[subnet, purpose, requested_by,
    allocated_by, status], ...]. Validates CIDR, pool membership and overlaps
    (against the DB and within the batch). Returns per-line results.
    """
    from db_utils import get_pool_key, get_used_subnets_db
    imported, errors = [], []
    existing = {}
    for p in POOLS:
        existing[p] = []
        for s in get_used_subnets_db(p):
            try:
                existing[p].append(ipaddress.ip_network(s))
            except ValueError:
                continue
    for i, parts in enumerate(rows, 1):
        subnet_raw, purpose, req_by, alloc_by, status = ([str(x).strip() for x in parts] + [""] * 5)[:5]
        if not subnet_raw:
            continue
        status = (status or "used").lower()
        if status not in ("used", "reserved"):
            errors.append(f"line {i}: status '{status}' must be 'used' or 'reserved' — skipped")
            continue
        try:
            net = ipaddress.ip_network(subnet_raw, strict=False)
        except ValueError:
            errors.append(f"line {i}: '{subnet_raw}' is not a valid CIDR")
            continue
        pool = get_pool_key(str(net))
        if not pool:
            errors.append(f"line {i}: {net} is outside the managed pools ({', '.join(POOLS.values())})")
            continue
        if any(net.overlaps(e) for e in existing[pool]):
            errors.append(f"line {i}: {net} overlaps an existing/imported subnet — skipped")
            continue
        db.session.add(SubnetRecord(
            subnet=str(net), pool=pool, status=status,
            purpose=purpose or None, requested_by=req_by or None,
            allocated_by=alloc_by or current_actor(), allocated_at=datetime.utcnow()))
        existing[pool].append(net)
        imported.append(str(net))
    db.session.commit()
    if imported:
        audit.record("inventory_imported", actor=current_actor(), actor_role="admin",
                     summary=f"Imported {len(imported)} subnet(s) into the inventory "
                             f"({len(errors)} line(s) skipped)",
                     data={"count": len(imported), "skipped": len(errors)})
    return {"imported": imported, "errors": errors}


@app.route("/admin/inventory", methods=["GET", "POST"])
@require_admin
def admin_inventory():
    """
    Post-deployment onboarding: the app ships with an EMPTY inventory — the
    admin loads the environment's real allocation state here (paste or Excel)
    so the allocator never hands out ranges that are already in use.
    """
    results = None
    if request.method == "POST":
        rows = []
        f = request.files.get("file")
        if f and f.filename:
            try:
                import pandas as pd
                if f.filename.lower().endswith((".xlsx", ".xls")):
                    df = pd.read_excel(f, dtype=str).fillna("")
                else:
                    df = pd.read_csv(f, dtype=str).fillna("")
                df.columns = [c.strip().replace(" ", "") for c in df.columns]
                if "Subnet" not in df.columns:
                    results = {"imported": [], "errors": ["File needs a 'Subnet' column "
                              "(optional: Purpose, RequestedBy, AllocatedBy, Status)."]}
                else:
                    for _, row in df.iterrows():
                        rows.append([row.get("Subnet", ""), row.get("Purpose", ""),
                                     row.get("RequestedBy", ""), row.get("AllocatedBy", ""),
                                     row.get("Status", "used")])
            except Exception as exc:
                results = {"imported": [], "errors": [f"Could not read file: {exc}"]}
        for raw in (request.form.get("entries") or "").splitlines():
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            rows.append([p.strip() for p in raw.split(",")])
        if results is None:
            results = _import_inventory(rows) if rows else \
                      {"imported": [], "errors": ["Nothing to import — paste entries or attach a file."]}
    return render_template("inventory_import.html", results=results,
                           inventory_empty=SubnetRecord.query.count() == 0,
                           record_count=SubnetRecord.query.count(), pools=POOLS)


@app.route("/allocator/<pool_key>")
@require_subnet_access
def allocator(pool_key):
    if pool_key not in POOLS:
        pool_key = DEFAULT_POOL
    return render_template("allocator.html", pool_key=pool_key, base_cidr=POOLS[pool_key])


# ═══════════════════════════════════════════════════════════════════════════
# Subnet APIs (unchanged)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/pool_stats")
@require_subnet_access
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
@require_subnet_access
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
@require_subnet_access
def allocate():
    pool, base_net = get_pool_from_request()
    selected     = request.form.get("selected")
    purpose      = request.form.get("purpose", "").strip()
    requested_by = request.form.get("requested_by", "").strip()
    allocated_by = request.form.get("allocated_by", "").strip()
    if not all([selected, purpose, requested_by, allocated_by]):
        return jsonify({"error": "All fields are required"}), 400
    success, msg = allocate_subnet(selected, base_net, pool, purpose, requested_by, allocated_by)
    if success:
        audit.record("subnet_allocated", actor=current_actor(), actor_role="admin",
                     summary=f"Allocated {selected} ({purpose}) for {requested_by}",
                     data={"subnet": selected, "pool": pool, "allocated_by": allocated_by})
        changes.record(action="subnet_allocated", actor=current_actor(),
                       target=f"CIDR {selected}", summary=f"Allocated for {requested_by} ({purpose})",
                       before=None, after={"subnet": selected, "pool": pool, "purpose": purpose},
                       revert_op="release_cidr", revert_params={"subnet": selected})
    return jsonify({"error": msg} if not success else {"message": msg}), (400 if not success else 200)


@app.route("/deallocate", methods=["POST"])
@require_subnet_access
def deallocate():
    pool, base_net = get_pool_from_request()
    selected = request.form.get("selected")
    if not selected:
        return jsonify({"error": "No subnet selected"}), 400
    rec = SubnetRecord.query.filter_by(subnet=selected).first()
    snapshot = ({"subnet": rec.subnet, "pool": rec.pool, "purpose": rec.purpose or "",
                 "requested_by": rec.requested_by or "", "allocated_by": rec.allocated_by or ""}
                if rec else None)
    success, msg = deallocate_subnet(selected, base_net)
    if success:
        audit.record("subnet_deallocated", actor=current_actor(), actor_role="admin",
                     summary=f"Deallocated {selected}", data={"subnet": selected, "pool": pool})
        if snapshot:
            changes.record(action="subnet_deallocated", actor=current_actor(),
                           target=f"CIDR {selected}", summary=f"Deallocated {selected}",
                           before=snapshot, after=None,
                           revert_op="allocate_cidr", revert_params=snapshot)
    return jsonify({"error": msg} if not success else {"message": msg}), (400 if not success else 200)


@app.route("/all_available")
@require_subnet_access
def all_available():
    pool, base_net = get_pool_from_request()
    free_blocks = compute_free_blocks(pool, base_net)
    return jsonify({"available": [{"Subnet": str(n), "Purpose": ""} for n in free_blocks]})


@app.route("/available_base")
@require_subnet_access
def available_base_route():
    pool, base_net = get_pool_from_request()
    return jsonify({"available": [str(n) for n in compute_free_blocks(pool, base_net)]})


@app.route("/allocated")
@require_subnet_access
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
@require_subnet_access
def summary_unused_route():
    pool, base_net = get_pool_from_request()
    free_blocks = compute_free_blocks(pool, base_net)
    by_prefix = {}
    for n in free_blocks:
        by_prefix[n.prefixlen] = by_prefix.get(n.prefixlen, 0) + 1
    return jsonify({"total_unused": len(free_blocks), "by_prefix": by_prefix})


@app.route("/free_summary")
@require_subnet_access
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
    history = audit.list_entries(request_id=req_id, limit=50)
    vi = req.vnet_info
    peering_names = None
    if vi and vi.vnet_name:
        stored = req.get_details().get("peering_names") or {}
        peering_names = {
            "spoke_to_hub": stored.get("spoke_to_hub")
                            or render_name("TPL_PEERING_SPOKE_TO_HUB", vnet=vi.vnet_name),
            "hub_to_spoke": stored.get("hub_to_spoke")
                            or render_name("TPL_PEERING_HUB_TO_SPOKE", vnet=vi.vnet_name),
        }
    return render_template("request_detail.html", req=req, RequestStatus=RequestStatus,
                           history=history, done_actions=_done_actions(req),
                           spoke_default_routes=_spoke_default_routes(),
                           fw_private_ip=cfg.HUB_FIREWALL_PRIVATE_IP,
                           peering_names=peering_names)


@app.route("/requests/<int:req_id>/complete-manual", methods=["POST"])
@require_admin
def request_complete_manual(req_id):
    """
    Escape hatch: mark a request completed when the work was done manually
    outside the portal. Requires a note saying what was done; fully audited.
    """
    req = SpokeRequest.query.get_or_404(req_id)
    if req.status in RequestType.TERMINALS:
        return jsonify({"error": f"Request is already {req.status_label()}."}), 400
    final = req.workflow()[-1]
    if req.status == final:
        return jsonify({"error": "Request is already completed."}), 400
    note = str((request.get_json(force=True) or {}).get("note", "")).strip()
    if not note:
        return jsonify({"error": "Please describe what was done manually outside the portal."}), 400

    old = req.status
    req.status = final
    req.updated_at = datetime.utcnow()
    stamp = (f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC] Completed manually outside "
             f"the portal by {current_actor()}: {note}")
    req.notes = f"{req.notes}\n{stamp}" if req.notes else stamp
    db.session.commit()
    audit.record("completed_manually", actor=current_actor(), actor_role="admin", request_id=req.id,
                 summary=f"Marked {RequestStatus.label(final)} — done manually outside the portal: "
                         f"{note[:200]}",
                 data={"old": old, "new": final, "note": note[:400]})
    try:
        notifications.notify_status_changed(req)
    except Exception:
        pass
    return jsonify({"success": True, "status": final,
                    "message": f"Marked {RequestStatus.label(final)} (manual completion noted)."})


@app.route("/admin/audit")
@require_superadmin
def admin_audit():
    f_actor  = request.args.get("actor", "").strip()
    f_action = request.args.get("action", "").strip()
    f_req    = request.args.get("request_id", "").strip()
    f_q      = request.args.get("q", "").strip()
    entries = audit.list_entries(
        request_id=int(f_req) if f_req.isdigit() else None,
        actor=f_actor or None, action=f_action or None, q=f_q or None, limit=200,
    )
    return render_template("audit.html", entries=entries, actions=audit.distinct_actions(),
                           f_actor=f_actor, f_action=f_action, f_req=f_req, f_q=f_q)


@app.route("/admin/changes")
@require_admin
def admin_changes():
    """Change ledger — every recorded mutation with its before-state, and the
    dedicated place to revert any of them."""
    f_req = request.args.get("request_id", "").strip()
    f_status = request.args.get("status", "").strip()
    entries = changes.list_changes(
        request_id=int(f_req) if f_req.isdigit() else None,
        status=f_status or None, limit=200)
    return render_template("changes.html", entries=entries, f_req=f_req, f_status=f_status)


@app.route("/api/admin/changes/<int:cid>/revert", methods=["POST"])
@require_admin
def admin_change_revert(cid):
    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason", "")).strip()
    if not reason:
        return jsonify({"error": "A reason for the revert is required."}), 400
    res = changes.execute_revert(cid, actor=current_actor(), reason=reason)
    # Reflect the revert on the originating ticket so it's visible there.
    if res.get("success") and res.get("request_id"):
        req = SpokeRequest.query.get(res["request_id"])
        if req:
            stamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
            note = (f"[{stamp} UTC] Deployed change reverted by {current_actor()} — "
                    f"{res.get('change_summary', '')[:160]} · reason: {reason}")
            req.notes = f"{req.notes}\n{note}" if req.notes else note
            req.updated_at = datetime.utcnow()
            db.session.commit()
    return jsonify(res), (200 if res.get("success") else 400)


@app.route("/admin/search")
@require_admin
def admin_search():
    q = request.args.get("q", "").strip()
    results = search.global_search(q) if q else {}
    # Audit results are super-admin-only (mirrors the Audit trail restriction).
    if results and not session.get("is_superadmin"):
        results["audit"] = []
    total = sum(len(v) for v in results.values())
    return render_template("search_results.html", q=q, results=results, total=total)


@app.route("/requests/<int:req_id>/update-status", methods=["POST"])
@require_admin
def request_update_status(req_id):
    req = SpokeRequest.query.get_or_404(req_id)
    new_status = request.form.get("status", "").strip()
    # Valid targets = the request type's own workflow steps + terminal states
    valid = list(req.workflow()) + RequestType.TERMINALS
    if new_status not in valid:
        return jsonify({"error": "Invalid status for this request type"}), 400

    old_status = req.status
    req.status = new_status
    req.updated_at = datetime.utcnow()
    db.session.commit()
    audit.record("status_changed", actor=current_actor(), actor_role="admin", request_id=req.id,
                 summary=f"Status: {RequestStatus.label(old_status)} → {RequestStatus.label(new_status)}",
                 data={"old": old_status, "new": new_status})

    try:
        if req.request_type in (None, RequestType.VNET_NEW, RequestType.HUB_INTEGRATION):
            # Keep the richer VNET-workflow notifications where they apply
            if new_status == RequestStatus.HUB_INTEGRATION_IN_PROGRESS:
                notifications.notify_hub_in_progress(req)
            elif new_status == RequestStatus.HUB_INTEGRATED:
                notifications.notify_hub_integrated(req)
            elif req.request_type == RequestType.HUB_INTEGRATION:
                notifications.notify_status_changed(req)
        else:
            notifications.notify_status_changed(req)
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
        audit.record("vnet_info_updated", actor=current_actor(), actor_role="admin", request_id=req.id,
                     summary=f"VNET info edited: {vi.vnet_name or '—'} ({vi.resource_group or '—'})",
                     data={"vnet_name": vi.vnet_name, "resource_group": vi.resource_group,
                           "subscription_id": vi.subscription_id, "region": vi.region})
        return redirect(url_for("request_detail", req_id=req.id))
    return render_template("vnet_form.html", req=req, errors=[], form={})


# ── Health check (unauthenticated, for K8s probes) ──────────────────────────

@app.route("/health")
def health():
    try:
        db.session.execute(db.text("SELECT 1"))
        count = SpokeRequest.query.count()
        return jsonify({"status": "ok", "db": db_backend.safe_uri(),
                        "backend": "postgres" if db_backend.IS_POSTGRES else "sqlite",
                        "request_count": count}), 200
    except Exception as exc:
        return jsonify({"status": "error", "detail": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# Requester Agent (public)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/help/requester")
@require_login
def help_requester():
    return render_template("help_requester.html")


@app.route("/help/admin")
@require_admin
def help_admin():
    return render_template("help_admin.html")


# ── Reachability Tester (IT team) ───────────────────────────────────────────

@app.route("/it/zpa-analyzer")
@require_itadmin
def it_reachability():
    import reachability
    return render_template("reachability.html",
                           rnd_ready=reachability.configured("rnd"),
                           rnd2_ready=reachability.configured("rnd", "secondary"),
                           nmo_ready=reachability.configured("nmo"),
                           nmo2_ready=reachability.configured("nmo", "secondary"))


@app.route("/api/it/reachability", methods=["POST"])
@require_itadmin
def it_reachability_run():
    import reachability
    data = request.get_json(force=True) or {}
    res = reachability.run_check(
        source=str(data.get("source", "")).strip(),
        method=str(data.get("method", "")).strip(),
        target=str(data.get("target", "")).strip(),
        port=data.get("port"),
        instance=str(data.get("instance", "primary")).strip() or "primary",
    )
    if res.get("success"):
        audit.record("reachability_check", actor=current_actor(), actor_role="admin",
                     summary=f"{res.get('source')} → {res.get('method')} {res.get('target')} "
                             f"→ {res.get('verdict')} (exit {res.get('exit_code')})")
    return jsonify(res), (200 if res.get("success") else 400)


@app.route("/api/it/connector-health")
@require_itadmin
def it_connector_health():
    """Health dashboard: are the connector VMs (primary + secondary) up?"""
    import reachability
    return jsonify({"vms": reachability.health_all()})


@app.route("/api/it/connector-status", methods=["POST"])
@require_itadmin
def it_connector_status():
    """Richer per-VM diagnostics for the dashboard's 'More status'."""
    import reachability
    data = request.get_json(force=True) or {}
    res = reachability.vm_status(
        source=str(data.get("source", "")).strip(),
        instance=str(data.get("instance", "primary")).strip() or "primary")
    return jsonify(res), (200 if res.get("success") else 400)


# ── Live Azure lookups for the request forms (read-only) ───────────────────

@app.route("/api/azure/aks-options")
@require_login
def azure_aks_options():
    """Current AKS versions, node sizes and tiers for a subscription/region."""
    import azure_tools
    sub = (request.args.get("subscription", "").strip()
           or cfg.SPOKE_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID)
    region = request.args.get("region", "").strip() or AKS_STANDARD_REGION
    if not sub:
        return jsonify({"error": "Enter a Subscription ID first."}), 400
    ver = azure_tools.list_aks_versions(sub, region)
    siz = azure_tools.list_vm_sizes(sub, region)
    return jsonify({
        "region": region, "tiers": azure_tools.aks_tiers(),
        "versions": ver.get("versions", []), "versions_error": None if ver.get("success") else ver.get("message"),
        "sizes": siz.get("sizes", []), "sizes_total": siz.get("total"),
        "sizes_error": None if siz.get("success") else siz.get("message"),
    })


@app.route("/api/azure/regions")
@require_login
def azure_regions():
    """Azure regions available to a subscription (for the region picker)."""
    import azure_tools
    sub = (request.args.get("subscription", "").strip()
           or cfg.SPOKE_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID)
    if not sub:
        return jsonify({"error": "Enter a Subscription ID first."}), 400
    res = azure_tools.list_locations(sub)
    return jsonify(res), (200 if res.get("success") else 400)


@app.route("/api/azure/vnets")
@require_login
def azure_vnets():
    """VNets visible in a subscription (for the VNet picker)."""
    import azure_tools
    sub = request.args.get("subscription", "").strip()
    if not sub:
        return jsonify({"error": "Enter a Subscription ID first."}), 400
    res = azure_tools.list_vnets(sub)
    return jsonify(res), (200 if res.get("success") else 400)


@app.route("/api/admin/firewall/lookup")
@require_admin
def admin_firewall_lookup():
    """Find firewall rules that apply. With both source and destination, matches
    the src→dst pair (source side covers source AND destination side covers
    destination); otherwise falls back to a single-address (coverage) lookup."""
    import azure_tools
    src = request.args.get("source", "").strip()
    dst = request.args.get("destination", "").strip()
    if src and dst:
        res = azure_tools.find_firewall_rules_for_pair(src, dst)
    else:
        res = azure_tools.find_firewall_rules_for_address(
            request.args.get("address", "").strip() or src or dst)
    return jsonify(res), (200 if res.get("success") else 400)


# ── Subscription cost dashboard (separate cost service principal) ──────────

@app.route("/cost")
@require_superadmin
def cost_dashboard():
    import costmgmt
    return render_template("cost.html", cost_configured=costmgmt.configured(),
                           currency=cfg.COST_CURRENCY or "$")


@app.route("/api/cost/summary")
@require_superadmin
def api_cost_summary():
    import costmgmt
    if not costmgmt.configured():
        return jsonify({"error": "Cost service principal not configured (Settings → Cost / Billing)."}), 400
    try:
        return jsonify({"success": True, **costmgmt.summary(request.args.get("timeframe", "MonthToDate"))})
    except Exception as exc:
        log.exception("cost summary failed")
        return jsonify({"error": str(exc)[:200]}), 500


@app.route("/api/cost/breakdown")
@require_superadmin
def api_cost_breakdown():
    import costmgmt
    if not costmgmt.configured():
        return jsonify({"error": "Cost service principal not configured."}), 400
    sub = request.args.get("subscription", "").strip()
    if not sub:
        return jsonify({"error": "A subscription is required."}), 400
    by = request.args.get("by", "service")
    dim = "ResourceGroupName" if by == "rg" else "ServiceName"
    try:
        return jsonify({"success": True, "by": by,
                        **costmgmt.cost_by_dimension(sub, dim, request.args.get("timeframe", "MonthToDate"))})
    except Exception as exc:
        log.exception("cost breakdown failed")
        return jsonify({"error": str(exc)[:200]}), 500


@app.route("/api/cost/trend")
@require_superadmin
def api_cost_trend():
    import costmgmt
    if not costmgmt.configured():
        return jsonify({"error": "Cost service principal not configured."}), 400
    sub = request.args.get("subscription", "").strip()
    if not sub:
        return jsonify({"error": "A subscription is required."}), 400
    try:
        return jsonify({"success": True, **costmgmt.cost_daily(sub, request.args.get("timeframe", "MonthToDate"))})
    except Exception as exc:
        log.exception("cost trend failed")
        return jsonify({"error": str(exc)[:200]}), 500


@app.route("/api/admin/settings/test-cost", methods=["POST"])
@require_superadmin
def admin_settings_test_cost():
    import costmgmt
    return jsonify(costmgmt.test_connection())


# ── Subscription inventory (Azure facts + owner/budget metadata) ───────────

def _inventory_data():
    """Fast path: just the subscription list + stored owner/budget metadata.

    Deliberately does NOT run the (slow) per-subscription Cost Management queries —
    the page renders immediately from this, then pulls spend from
    /api/subscriptions/spend so a slow cost API never blocks the whole page.
    """
    import subinventory, costmgmt, azure_tools
    stored = subinventory.all_records()
    cost_available = costmgmt.configured()
    subs = []
    if cost_available:
        try:
            subs = [{**x, "spend": None} for x in costmgmt.list_subscriptions()]
        except Exception:
            log.exception("inventory: cost SP subscription list failed")
            cost_available = False
    if not subs:
        r = azure_tools.list_subscriptions()
        subs = [{**x, "spend": None} for x in (r.get("subscriptions", []) if r.get("success") else [])]
    for sub in subs:
        inv = stored.get(sub["id"], {})
        sub["inventory"] = {k: (inv.get(k) or "") for k in subinventory.FIELDS}
        sub["updated_by"], sub["updated_ts"] = inv.get("updated_by"), inv.get("updated_ts")
    return {"subscriptions": subs, "currency": cfg.COST_CURRENCY or "", "cost_available": cost_available}


@app.route("/subscriptions")
@require_superadmin
def subscription_inventory_page():
    return render_template("subscriptions.html", currency=cfg.COST_CURRENCY or "$")


@app.route("/api/subscriptions/inventory")
@require_superadmin
def api_subscription_inventory():
    try:
        return jsonify({"success": True, **_inventory_data()})
    except Exception as exc:
        log.exception("subscription inventory failed")
        return jsonify({"error": str(exc)[:200]}), 500


@app.route("/api/subscriptions/spend")
@require_superadmin
def api_subscription_spend():
    """Per-subscription month-to-date spend, loaded lazily by the inventory page so
    the slow Cost Management queries never block the initial render."""
    import costmgmt
    if not costmgmt.configured():
        return jsonify({"success": True, "cost_available": False, "spend": {}, "currency": ""})
    try:
        s = costmgmt.summary("MonthToDate")
        spend = {x["id"]: x.get("cost") for x in s.get("subscriptions", [])}
        return jsonify({"success": True, "cost_available": True, "spend": spend,
                        "currency": s.get("currency", "")})
    except Exception as exc:
        log.exception("subscription spend failed")
        return jsonify({"success": False, "error": str(exc)[:200]}), 500


@app.route("/api/subscriptions/inventory", methods=["POST"])
@require_superadmin
def api_subscription_inventory_save():
    import subinventory
    data = request.get_json(force=True) or {}
    sid = str(data.get("subscription_id", "")).strip()
    if not sid:
        return jsonify({"error": "subscription_id is required."}), 400
    subinventory.upsert(sid, data, actor=current_actor())
    audit.record("subscription_inventory", actor=current_actor(), actor_role="admin",
                 summary=f"Updated inventory for subscription {sid}",
                 data={"subscription_id": sid})
    return jsonify({"success": True})


@app.route("/api/admin/requests/<int:req_id>/diagnose", methods=["POST"])
@require_admin
def request_diagnose(req_id):
    """Run the source→destination connectivity diagnosis for a network-issue
    request (routing / DNS / firewall / return path), optionally with live tests."""
    req = SpokeRequest.query.get_or_404(req_id)
    if (req.request_type or "") != RequestType.NETWORK_ISSUE:
        return jsonify({"error": "Diagnosis is only available for network-issue requests."}), 400
    import netdiag
    run_live = bool((request.get_json(silent=True) or {}).get("run_live"))
    try:
        report = netdiag.diagnose(req.get_details(), run_live=run_live)
    except Exception as exc:
        log.exception("network diagnosis failed")
        return jsonify({"error": f"Diagnosis failed: {exc}"}), 500
    # Plain-language explanation + recommended fix from the LLM (best-effort).
    report["ai_summary"] = netdiag.summarize(report, req.get_details())
    audit.record("network_diagnosis", actor=current_actor(), actor_role="admin", request_id=req.id,
                 summary=f"Connectivity diagnosis — verdict: {report.get('verdict')}",
                 data={"verdict": report.get("verdict"), "run_live": run_live,
                       "ai": bool(report.get("ai_summary"))})
    if req.status == RequestStatus.SUBMITTED:
        req.status = RequestStatus.IN_PROGRESS
        req.updated_at = datetime.utcnow()
        db.session.commit()
    return jsonify({"success": True, **report})


@app.route("/api/azure/subnets")
@require_login
def azure_subnets():
    """Subnets in a chosen VNet (for the subnet picker)."""
    import azure_tools
    res = azure_tools.list_subnets(
        request.args.get("subscription", "").strip(),
        request.args.get("resource_group", "").strip(),
        request.args.get("vnet", "").strip())
    return jsonify(res), (200 if res.get("success") else 400)


@app.route("/requester")
@require_login
def requester_page():
    session.setdefault("requester_history", [])
    return render_template("requester.html")


@app.route("/requester/clear", methods=["POST"])
@require_login
def requester_clear():
    session.pop("requester_history", None)
    return jsonify({"message": "Conversation cleared."})


# ── Form API endpoints (no agent — direct DB writes) ────────────────────────

# Required keys inside `details` for each non-VNET request type
TYPE_REQUIRED_DETAILS = {
    RequestType.FIREWALL_POLICY:   ["action", "rule_kind", "source", "destination", "justification"],
    RequestType.HUB_INTEGRATION:   ["subscription_id", "resource_group", "vnet_name",
                                    "region", "address_space"],
    RequestType.ZPA_RND_ROUTING:   ["spoke_vnet_name", "spoke_cidr"],
    RequestType.ZPA_OTHER_ROUTING: ["spoke_vnet_name", "spoke_cidr", "connector_name"],
    RequestType.ZPA_NMO_ROUTING:   ["spoke_vnet_name", "spoke_cidr"],
    RequestType.SUBNET_ADDITIONAL: ["vnet_name", "subnet_size"],
    RequestType.VNET_DECOMMISSION: ["vnet_name", "resource_group", "confirm",
                                    "created_by_admin", "manual_changes"],
    RequestType.DNS:               ["dns_kind", "zone"],
    RequestType.AKS_CLUSTER:       ["cluster_name", "resource_group", "subscription_id",
                                    "vnet_name", "subnet_name", "node_pool_name", "tier",
                                    "zpa_rnd_access"],
    RequestType.NETWORK_ISSUE:     ["issue", "source", "destination"],
    RequestType.OTHER:             ["description"],
}


def _create_service_request(request_type, purpose, requester_name, requester_email, details):
    """
    Shared creation path for non-VNET request types (used by the form API and the
    requester agent). Returns (result_dict, http_status).
    """
    from db_utils import create_spoke_request, get_spoke_request, upsert_vnet_info
    if request_type not in RequestType.ALL or request_type == RequestType.VNET_NEW:
        return {"error": f"Unknown request type '{request_type}'."}, 400
    # Keycloak identity wins (and ties the request to the signed-in requester).
    requester_name, requester_email = _sso_identity(requester_name, requester_email)
    if not purpose or not requester_name:
        return {"error": "Name and a short summary/purpose are required."}, 400

    details = {k: v for k, v in (details or {}).items() if v not in (None, "")}
    # Business justification is mandatory for every request type, portal-wide.
    if not str(details.get("justification") or "").strip():
        return {"error": "A business justification is required."}, 400
    missing = [k for k in TYPE_REQUIRED_DETAILS.get(request_type, []) if not details.get(k)]
    if missing:
        return {"error": "Missing required fields: " + ", ".join(missing)}, 400

    # Firewall guard: catch bad input at submission time, not at apply time.
    if request_type == RequestType.FIREWALL_POLICY:
        _s, dests, _ip, _po, _ap, perrors = _fw_params(details)
        fw_action = (details.get("action") or "add").lower()
        # The existing rule name is required only when modifying a rule.
        if fw_action == "modify" and not details.get("rule_name"):
            return {"error": "Existing rule name is required to modify a rule."}, 400
        if perrors and fw_action in ("add", "modify"):
            return {"error": "; ".join(perrors)}, 400
        if (details.get("rule_kind") == "application" and fw_action in ("add", "modify")):
            bad = _fqdn_errors(dests)
            if bad:
                return {"error": "Application rules only accept FQDN destinations "
                                 "(e.g. *.example.com, *.presight.ai) — these are IP addresses: "
                                 + ", ".join(bad) + ". Choose a network rule for IP destinations."}, 400

    # DNS guard: kind-specific required fields, and the hub-availability rules
    # are re-verified server-side (the form's check button is mandatory, but
    # never trust the client).
    if request_type == RequestType.DNS:
        import azure_tools
        kind = details.get("dns_kind", "")
        if kind == "record_add":
            missing = [k for k in ("record_type", "record_name", "record_value",
                                   "record_description") if not details.get(k)]
            if missing:
                return {"error": "A record request needs: " + ", ".join(missing)}, 400
        elif kind == "zone_link_to_hub":
            chk = azure_tools.check_private_dns_zone(details.get("zone", ""))
            if not chk.get("success"):
                return {"error": "Could not verify zone availability — " + chk.get("message", "")}, 400
            if chk.get("hub_linked"):
                return {"error": f"Zone '{details.get('zone')}' is already linked to the hub — "
                                 f"a duplicate link isn't possible. If you need records in it, "
                                 f"raise an 'Add A/CNAME record' DNS request instead."}, 400
        elif kind == "hub_zone_link_to_vnet":
            chk = azure_tools.check_private_dns_zone(details.get("zone", ""))
            if not chk.get("success"):
                return {"error": "Could not verify zone availability — " + chk.get("message", "")}, 400
            if not chk.get("exists"):
                return {"error": f"Zone '{details.get('zone')}' was not found in the hub — check the "
                                 f"name, or raise a 'Link my private DNS zone to the Hub' request."}, 400
            missing = [k for k in ("subscription_id", "resource_group", "vnet_name")
                       if not details.get(k)]
            if missing:
                return {"error": "Hub-zone link needs your VNET details: " + ", ".join(missing)}, 400
        else:
            return {"error": "Pick a DNS request kind."}, 400

    # AKS guard: validate node sizing at submission time.
    if request_type == RequestType.AKS_CLUSTER:
        import re as _re_aks
        if not _re_aks.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,61}[A-Za-z0-9]$",
                             details.get("cluster_name", "")):
            return {"error": "Cluster name must be 2–63 characters: letters, digits, '-', '_' or '.'."}, 400
        if details.get("tier") not in ("Free", "Standard", "Premium"):
            return {"error": "Pick a cluster tier: Free, Standard or Premium."}, 400
        region = (details.get("region") or AKS_STANDARD_REGION).strip()
        if region != AKS_STANDARD_REGION and not (details.get("region_justification") or "").strip():
            return {"error": f"A justification is required to deploy outside the standard "
                             f"region ({AKS_STANDARD_REGION})."}, 400
        scale = (details.get("autoscaling") or "disabled").lower()
        if scale == "enabled":
            try:
                mn, mx = int(details.get("min_count") or 0), int(details.get("max_count") or 0)
            except ValueError:
                return {"error": "Autoscale min/max node counts must be whole numbers."}, 400
            if mn < 1 or mx < mn:
                return {"error": "Autoscaling needs min ≥ 1 and max ≥ min."}, 400
        else:
            try:
                nc = int(details.get("node_count") or 0)
            except ValueError:
                return {"error": "Node count must be a whole number."}, 400
            if nc < 1:
                return {"error": "Node count must be at least 1."}, 400

    # Decommission guard: manual changes made outside the portal must be removed
    # by the requester before the admin will decommission the VNET.
    if (request_type == RequestType.VNET_DECOMMISSION
            and details.get("manual_changes") == "yes"
            and not details.get("manual_changes_removed")):
        return {"error": "You reported manual changes outside this portal. Please remove them "
                         "(extra subnets, NSGs, private endpoints, attached devices, peerings…) "
                         "and then confirm removal before submitting the decommission request."}, 400

    req_id = create_spoke_request(
        cidr_needed="", purpose=purpose, requester_name=requester_name, ip_range="",
        hub_integration=(request_type == RequestType.HUB_INTEGRATION),
        requester_email=requester_email or None,
        request_type=request_type, details=details,
    )

    # Hub-integration requests carry full VNET details — mirror them into vnet_info
    # and allocated_subnet so the admin's existing Azure buttons (peer/internet/
    # routes) work without special-casing.
    if request_type == RequestType.HUB_INTEGRATION:
        from db_utils import update_spoke_request
        upsert_vnet_info(req_id,
                         subscription_id=details.get("subscription_id"),
                         resource_group=details.get("resource_group"),
                         vnet_name=details.get("vnet_name"),
                         region=details.get("region"),
                         address_space=details.get("address_space"))
        update_spoke_request(req_id, allocated_subnet=details.get("address_space"))

    req = get_spoke_request(req_id)
    audit.record("request_created", actor=requester_name, actor_role="requester", request_id=req_id,
                 summary=f"{RequestType.label(request_type)} request submitted: {purpose[:120]}",
                 data={"request_type": request_type, "details": details})
    try:
        notifications.notify_request_submitted(req)
    except Exception:
        pass
    # Network-issue tickets: run the connectivity diagnosis in the background so
    # the admin sees findings the moment they open the request.
    if request_type == RequestType.NETWORK_ISSUE:
        _spawn_network_diagnosis(req_id, details)
    return {"success": True, "request_id": req_id}, 200


def _spawn_network_diagnosis(req_id, details):
    """Compute the connectivity diagnosis off the request thread and store it on
    the ticket (control-plane only — no live tests / no LLM at submit time)."""
    import threading

    def _run():
        with app.app_context():
            try:
                import netdiag
                report = netdiag.diagnose(details, run_live=False)
                r = SpokeRequest.query.get(req_id)
                if r:
                    d = r.get_details()
                    d["auto_diagnosis"] = {
                        "verdict": report.get("verdict"), "cause": report.get("cause"),
                        "steps": report.get("steps"),
                        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}
                    r.set_details(d)
                    db.session.commit()
            except Exception:
                log.exception("auto network diagnosis failed for #%s", req_id)

    threading.Thread(target=_run, daemon=True).start()


def _subnets_fit(vnet_prefix: int, subnets: list) -> bool:
    import azure_tools
    try:
        azure_tools.carve_subnets(f"10.0.0.0/{vnet_prefix}", subnets)
        return True
    except ValueError:
        return False


def _create_vnet_request(data: dict, actor: str = None, actor_role: str = "requester"):
    """
    Shared, validated creation path for New VNET requests — used by the form
    API and the requester agent. Returns (result_dict, http_status).
    """
    from db_utils import create_spoke_request, get_spoke_request
    cidr_needed   = str(data.get("cidr_needed", "")).strip()
    purpose       = str(data.get("purpose", "")).strip()
    justification = str(data.get("justification", "")).strip()
    requester_name = str(data.get("requester_name", "")).strip()
    requester_email = str(data.get("requester_email", "")).strip()
    # Keycloak identity wins (ties the request to the signed-in requester).
    requester_name, requester_email = _sso_identity(requester_name, requester_email)
    requester_email = requester_email or ""
    ip_range      = str(data.get("ip_range", "")).strip()
    hub_integration = bool(data.get("hub_integration", False))
    deployment_mode = str(data.get("deployment_mode", "self")).strip().lower()
    if deployment_mode not in ("self", "admin"):
        deployment_mode = "self"
    if not all([cidr_needed, purpose, requester_name, ip_range]):
        return {"error": "All fields are required."}, 400
    if not justification:
        return {"error": "A business justification is required."}, 400
    if ip_range not in ["10.110.0.0/16", "10.119.0.0/16"]:
        return {"error": "Invalid IP range."}, 400

    # When the requester wants the admin to deploy, the Azure target is required
    # up front so the admin can run the deploy without chasing details.
    azure = {k: str(data.get(k, "")).strip() for k in
             ("subscription_id", "resource_group", "vnet_name", "region",
              "subnet_name", "subnet_size", "subnet_purpose")}
    subnets = []
    for s in (data.get("subnets") or []):
        name, size = str(s.get("name", "")).strip(), str(s.get("size", "")).strip()
        if name or size:
            subnets.append({"name": name, "size": size,
                            "purpose": str(s.get("purpose", "")).strip()})
    if not subnets and azure["subnet_name"]:            # legacy single-subnet payload
        subnets = [{"name": azure["subnet_name"], "size": azure["subnet_size"],
                    "purpose": azure["subnet_purpose"]}]
    if deployment_mode == "admin":
        missing = [k for k in ("subscription_id", "resource_group", "vnet_name", "region")
                   if not azure[k]]
        if missing:
            return {"error": "Admin-deploy requires: " + ", ".join(missing)}, 400
        if not subnets:
            return {"error": "Admin-deploy requires at least one subnet."}, 400
        if any(not s["name"] or not s["size"] for s in subnets):
            return {"error": "Every subnet needs a name and a size."}, 400

    # The requested subnets must fit inside the requested VNET size — reject
    # NOW rather than failing at deployment time.
    if subnets and str(cidr_needed).isdigit():
        import azure_tools
        vnet_prefix = int(cidr_needed)
        too_big = [f"/{s['size']} ({s['name']})" for s in subnets
                   if str(s["size"]).isdigit() and int(s["size"]) < vnet_prefix]
        if too_big:
            return {"error": f"Subnet(s) larger than the /{vnet_prefix} VNET itself: "
                             + ", ".join(too_big) + ". Pick smaller subnets or a bigger VNET."}, 400
        try:
            azure_tools.carve_subnets(f"10.0.0.0/{vnet_prefix}", subnets)
        except ValueError:
            fits = next((p for p in range(vnet_prefix - 1, 15, -1)
                         if _subnets_fit(p, subnets)), None)
            suggest = (f" They would fit in a /{fits} — request that size instead, "
                       f"or use smaller/fewer subnets.") if fits else \
                      " Use smaller or fewer subnets."
            return {"error": f"The requested subnets do not fit inside a /{vnet_prefix} VNET."
                             + suggest}, 400

    # Internet access requested for the spoke (via hub firewall)
    internet_access = str(data.get("internet_access", "")).strip().lower()
    internet_dest = str(data.get("internet_dest", "")).strip()
    internet_ports = str(data.get("internet_ports", "")).strip()
    if internet_access and internet_access not in ("full", "network", "application", "none"):
        return {"error": "Invalid internet access option."}, 400
    if internet_access in ("network", "application"):
        _s, dests, _ip, _po, _ap, perrors = _fw_params(
            {"source": "", "destination": internet_dest, "ports_protocol": internet_ports})
        if perrors:
            return {"error": "; ".join(perrors)}, 400
        if not dests:
            return {"error": f"A {internet_access} internet rule needs destination(s)."}, 400
        if internet_access == "application":
            bad = _fqdn_errors(dests)
            if bad:
                return {"error": "Application rules only accept FQDN destinations "
                                 "(e.g. *.presight.ai) — these are IPs: " + ", ".join(bad)}, 400

    details_payload = {"justification": justification}
    if subnets:
        details_payload["subnets"] = subnets
    if internet_access:
        details_payload["internet_access"] = internet_access
        if internet_dest:
            details_payload["internet_dest"] = internet_dest
        if internet_ports:
            details_payload["internet_ports"] = internet_ports

    req_id = create_spoke_request(cidr_needed, purpose, requester_name, ip_range,
                                  hub_integration, requester_email=requester_email or None,
                                  deployment_mode=deployment_mode,
                                  details=details_payload or None)
    if deployment_mode == "admin":
        from db_utils import upsert_vnet_info
        first = subnets[0]
        upsert_vnet_info(req_id, subscription_id=azure["subscription_id"],
                         resource_group=azure["resource_group"], vnet_name=azure["vnet_name"],
                         region=azure["region"], subnet_name=first["name"],
                         subnet_size=first["size"], subnet_purpose=first["purpose"] or None)
    req = get_spoke_request(req_id)
    audit.record("request_created", actor=actor or requester_name, actor_role=actor_role,
                 request_id=req_id,
                 summary=f"New VNET request: /{cidr_needed} in {ip_range} — {purpose[:100]}",
                 data={"request_type": "vnet_new", "cidr_needed": cidr_needed,
                       "ip_range": ip_range, "deployment_mode": deployment_mode})
    try:
        notifications.notify_cidr_requested(req)
    except Exception:
        pass
    return {"success": True, "request_id": req_id}, 200


@app.route("/api/requester/new-request", methods=["POST"])
@require_login
def requester_new_request():
    data = request.get_json(force=True)

    # Non-VNET request types go through the shared service-request path
    request_type = str(data.get("request_type", RequestType.VNET_NEW)).strip() or RequestType.VNET_NEW
    if request_type != RequestType.VNET_NEW:
        try:
            result, code = _create_service_request(
                request_type=request_type,
                purpose=str(data.get("purpose", "")).strip(),
                requester_name=str(data.get("requester_name", "")).strip(),
                requester_email=str(data.get("requester_email", "")).strip(),
                details=data.get("details") or {},
            )
            return jsonify(result), code
        except Exception as exc:
            log.exception("Form: error creating %s request", request_type)
            return jsonify({"error": str(exc)}), 500

    try:
        result, code = _create_vnet_request(data)
        return jsonify(result), code
    except Exception as exc:
        log.exception("Form: error creating request")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/requester/dns-zone-check", methods=["POST"])
@require_login
def requester_dns_zone_check():
    """Availability check for DNS requests: is the zone already in the hub?
    Read-only; used (mandatorily) by the DNS link request kinds."""
    import azure_tools
    zone = str((request.get_json(force=True) or {}).get("zone", "")).strip()
    res = azure_tools.check_private_dns_zone(zone)
    return jsonify(res), (200 if res.get("success") else 400)


@app.route("/api/requester/status/<int:request_id>")
@require_login
def requester_get_status(request_id):
    from db_utils import get_spoke_request
    req = get_spoke_request(request_id)
    if not req:
        return jsonify({"error": f"Request #{request_id} not found."}), 404
    if not _owns_request(req):
        # Don't reveal existence of others' requests.
        return jsonify({"error": f"Request #{request_id} not found."}), 404
    return jsonify(req.to_dict())


@app.route("/api/requester/my-requests")
@require_login
def requester_my_requests():
    """The signed-in requester's own requests (SSO only). Empty in open mode."""
    if not session.get("sso"):
        return jsonify({"sso": False, "requests": []})
    name, email = _requester_owner()
    q = SpokeRequest.query
    if email:
        q = q.filter(db.func.lower(SpokeRequest.requester_email) == email.lower())
    elif name:
        q = q.filter(SpokeRequest.requester_name == name)
    else:
        return jsonify({"sso": True, "requests": []})
    rows = q.order_by(SpokeRequest.created_at.desc()).limit(200).all()
    return jsonify({"sso": True, "requests": [r.to_dict() for r in rows]})


@app.route("/api/requester/vnet-created", methods=["POST"])
@require_login
def requester_vnet_created():
    from db_utils import get_spoke_request, update_spoke_request, upsert_vnet_info
    data = request.get_json(force=True)
    request_id = data.get("request_id")
    if not request_id:
        return jsonify({"error": "Request ID is required."}), 400
    req = get_spoke_request(int(request_id))
    if not req:
        return jsonify({"error": f"Request #{request_id} not found."}), 404
    if req.status != RequestStatus.CIDR_ASSIGNED:
        return jsonify({"error": f"Status is '{req.status_label()}' — CIDR must be assigned first."}), 400

    # Capture the spoke VNET details — required for hub peering later.
    vnet = {k: str(data.get(k, "")).strip() for k in
            ("subscription_id", "resource_group", "vnet_name", "region", "address_space")}
    missing = [k for k in vnet if not vnet[k]]
    if missing:
        return jsonify({"error": "VNET details required for peering: " + ", ".join(missing)}), 400
    upsert_vnet_info(int(request_id), subscription_id=vnet["subscription_id"],
                     resource_group=vnet["resource_group"], vnet_name=vnet["vnet_name"],
                     region=vnet["region"], address_space=vnet["address_space"])
    update_spoke_request(int(request_id), status=RequestStatus.VNET_CREATED)
    req = get_spoke_request(int(request_id))
    audit.record("status_changed", actor=req.requester_name, actor_role="requester",
                 request_id=req.id,
                 summary=f"Status: CIDR Assigned → VNET Created ({vnet['vnet_name']})",
                 data={"old": RequestStatus.CIDR_ASSIGNED, "new": RequestStatus.VNET_CREATED, **vnet})
    try:
        notifications.notify_vnet_created(req)
    except Exception:
        pass
    return jsonify({"success": True, "message": f"Request #{request_id} updated to VNET Created."})


@app.route("/api/requester/reminder", methods=["POST"])
@require_login
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
    if ok:
        audit.record("reminder_sent", actor=req.requester_name, actor_role="requester",
                     request_id=req.id, summary=f"Reminder to admin: {message[:150]}")
    return jsonify({"success": ok})


@app.route("/api/requester/chats")
@require_login
def requester_chats_list():
    import chats
    return jsonify({"chats": chats.list_chats("requester", _chat_owner("requester"))})


@app.route("/api/requester/chats/<int:cid>")
@require_login
def requester_chat_get(cid):
    import chats
    if not chats.owns(cid, "requester", _chat_owner("requester")):
        return jsonify({"error": "Chat not found."}), 404
    ch = chats.get_chat(cid)
    return jsonify({"id": ch["id"], "title": ch["title"],
                    "messages": [{"role": m.get("role"), "content": m.get("content", "")}
                                 for m in ch["messages"]]})


@app.route("/api/requester/chats/<int:cid>", methods=["DELETE"])
@require_login
def requester_chat_delete(cid):
    import chats
    chats.delete_chat(cid, _chat_owner("requester"))
    return jsonify({"success": True})


@app.route("/api/requester/chat", methods=["POST"])
@require_login
def requester_chat():
    import chats
    data = request.get_json(force=True)
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "Empty message"}), 400

    owner = _chat_owner("requester")
    chat_id = data.get("chat_id")
    if not (chat_id and str(chat_id).isdigit() and chats.owns(int(chat_id), "requester", owner)):
        chat_id = chats.create_chat("requester", owner)
    chat_id = int(chat_id)
    ch = chats.get_chat(chat_id)
    history = [{"role": m.get("role"), "content": m.get("content", "")}
               for m in (ch["messages"] if ch else [])]
    history.append({"role": "user", "content": user_msg})

    reply, tool_calls = "Agent error.", []
    try:
        import agent_requester as ag
        result = ag.chat(history)
        reply = result.get("reply", "")
        for tc in result.get("tool_calls", []):
            tool_calls.append({"tool": str(tc.get("tool", "")), "status": str(tc.get("status", ""))})
    except Exception as exc:
        log.exception("Requester agent error")
        reply = f"Agent error: {exc}"

    chats.append_messages(chat_id,
                          [{"role": "user", "content": user_msg},
                           {"role": "assistant", "content": reply}],
                          title_hint=user_msg)
    return jsonify({"reply": reply, "tool_calls": tool_calls, "chat_id": chat_id})


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


def _fw_params(details: dict):
    """
    Parse the firewall-request details into SDK-ready parameters.

    ports_protocol accepts comma/semicolon-separated entries in any of these
    shapes: "TCP/443", "TCP 443", "TCP:443", "https:443", "http 8080", "443",
    "udp", "*". Unrecognised entries are returned as errors — they must never
    silently fall back to Any/*.

    Returns (sources, destinations, ip_protocols, ports, app_protocols, errors).
    """
    import re

    def _split(v):
        return [s.strip() for s in str(v or "").replace(";", ",").split(",") if s.strip()]

    sources = _split(details.get("source")) or ["*"]
    dests = _split(details.get("destination"))
    ip_protocols, ports, app_protocols, errors = [], [], [], []
    for part in _split(details.get("ports_protocol")):
        m = re.match(r"(?i)^([a-z]+)?[\s/:]*(\d+|\*)?$", part)
        proto = (m.group(1) or "").upper() if m else None
        port = (m.group(2) or "") if m else ""
        if not m or (not proto and not port):
            errors.append(f"Unrecognised ports/protocol entry: '{part}' "
                          f"(use e.g. TCP/443, https:443 or a bare port)")
            continue
        if proto in ("HTTP", "HTTPS"):
            app_protocols.append({
                "protocol_type": "Https" if proto == "HTTPS" else "Http",
                "port": int(port) if port.isdigit() else (443 if proto == "HTTPS" else 80)})
            if port and port not in ports:
                ports.append(port)
        elif proto in ("TCP", "UDP", "ICMP", "ANY"):
            p = "Any" if proto == "ANY" else proto
            if p not in ip_protocols:
                ip_protocols.append(p)
            if port and port not in ports:
                ports.append(port)
        elif not proto:                       # bare port number or "*"
            if port not in ports:
                ports.append(port)
            app_protocols.append({"protocol_type": "Https" if port in ("443", "*") else "Http",
                                  "port": int(port) if port.isdigit() else 443})
        else:
            errors.append(f"Unknown protocol '{proto}' in '{part}' "
                          f"(network: TCP/UDP/ICMP/Any; application: http/https)")
    return (sources, dests, ip_protocols or ["Any"], ports or ["*"],
            app_protocols or [{"protocol_type": "Https", "port": 443}], errors)


def _fqdn_errors(dests: list) -> list:
    """Application-rule targets must be FQDNs — IPs are rejected by Azure."""
    bad = []
    for d in dests:
        try:
            ipaddress.ip_network(str(d).strip(), strict=False)
            bad.append(d)
        except ValueError:
            pass
    return bad


@app.route("/api/admin/azure-action/<int:req_id>", methods=["POST"])
@require_admin
def admin_azure_action(req_id):
    """
    Run a single Azure onboarding action for a request:
      vnet          -> create the spoke VNET + subnet (admin-deploy requests)
      peer          -> peer spoke <-> hub
      internet      -> allow internet egress on the firewall policy
      gateway_route -> add a route to the spoke in the gateway routing table
      zpa_route     -> add a route to the spoke in the ZPA routing table
    """
    import azure_tools
    from naming import sanitize
    req = SpokeRequest.query.get_or_404(req_id)
    payload = request.get_json(force=True) or {}
    action = payload.get("action", "")
    on_conflict = str(payload.get("on_conflict", "")).strip()
    details = req.get_details()

    def _record_change(res, act=None):
        """Persist the mutation + its before-state into the change ledger."""
        ch = res.get("change")
        if res.get("success") and ch and not res.get("dry_run"):
            changes.record(action=act or action, actor=current_actor(), request_id=req.id,
                           target=ch.get("target", ""),
                           summary=str(res.get("message", ""))[:300],
                           before=ch.get("before"), after=ch.get("after"),
                           revert_op=ch.get("revert_op"),
                           revert_params=ch.get("revert_params"))

    def _audit_azure(res):
        # 'kept' also covers in-place updates of PRE-EXISTING config — either
        # way this request created nothing new, so there is nothing to revert.
        _record_change(res)
        audit.record("azure_action", actor=current_actor(), actor_role="admin", request_id=req.id,
                     summary=f"Azure action '{action}' — "
                             f"{'dry-run' if res.get('dry_run') else ('ok' if res.get('success') else 'FAILED')}",
                     data={"action": action, "dry_run": bool(res.get("dry_run")),
                           "success": bool(res.get("success")),
                           "kept": bool(res.get("kept_existing") or res.get("replaced_existing")),
                           "message": str(res.get("message", ""))[:400]})

    def _route_res(**kwargs):
        """Route additions with view/edit/proceed conflict handling."""
        if on_conflict == "keep":
            return {"success": True, "kept_existing": True,
                    "message": "Existing route kept as-is — step marked complete "
                               "without Azure changes."}
        return azure_tools.add_route_to_table(
            on_conflict=("replace" if on_conflict == "replace" else None), **kwargs)

    # ── ZPA routing actions — driven by request details, not vnet_info ──
    if action == "zpa_route_to_spoke":
        # Route to the spoke CIDR in the ZPA connector's routing table
        spoke_cidr = details.get("spoke_cidr", "")
        if not spoke_cidr:
            return jsonify({"error": "Request has no spoke CIDR in its details."}), 400
        table = details.get("connector_route_table") or cfg.UDR_ZPA_NAME
        if not table:
            return jsonify({"error": "No ZPA routing table configured "
                                     "(set 'ZPA route table' in Settings)."}), 400
        res = _route_res(
            route_table_name=table, resource_group=cfg.UDR_RESOURCE_GROUP,
            route_name=render_name("TPL_ROUTE_NAME", vnet=details.get("spoke_vnet_name", f"req{req.id}"),
                                   request_id=req.id),
            address_prefix=spoke_cidr, next_hop_type="VirtualAppliance",
            next_hop_ip=cfg.HUB_FIREWALL_PRIVATE_IP, subscription_id=cfg.HUB_SUBSCRIPTION_ID,
        )
        _audit_azure(res)
        _auto_advance(req)
        return jsonify(res), (200 if res.get("success") else 207)

    if action == "spoke_udr_zpa":
        # Route to the ZPA connection subnet in the spoke's own UDR
        zpa_subnet = cfg.ZPA_CONNECTION_SUBNET
        if not zpa_subnet:
            return jsonify({"error": "ZPA connection subnet not configured "
                                     "(set it in Settings → Routing / UDRs)."}), 400
        udr_name = details.get("spoke_udr_name", "")
        udr_rg   = details.get("spoke_udr_rg", "")
        if not udr_name or not udr_rg:
            return jsonify({"error": "Request details are missing the spoke UDR name/resource group."}), 400
        connector = details.get("connector_name", "rnd")
        res = _route_res(
            route_table_name=udr_name, resource_group=udr_rg,
            route_name=sanitize(f"to-zpa-{connector}"),
            address_prefix=zpa_subnet, next_hop_type="VirtualAppliance",
            next_hop_ip=cfg.HUB_FIREWALL_PRIVATE_IP,
            subscription_id=details.get("spoke_subscription_id") or cfg.SPOKE_SUBSCRIPTION_ID,
        )
        _audit_azure(res)
        _auto_advance(req)
        return jsonify(res), (200 if res.get("success") else 207)

    # ── Firewall policy actions — driven by request details ──
    if action in ("fw_check", "fw_ensure_policy", "fw_apply"):
        body = request.get_json(force=True) or {}
        rcg_sel = str(body.get("rcg", "")).strip()
        col_sel = str(body.get("collection", "")).strip()
        fw_action = (details.get("action") or "add").lower()
        rule_kind = (details.get("rule_kind") or "network").lower()
        rule_name = (details.get("rule_name") or "").strip() \
                    or sanitize(f"req-{req.id}-{rule_kind}-rule")
        sources, dests, ip_protocols, ports, app_protocols, perrors = _fw_params(details)

        if action == "fw_check":
            # Coverage is checked by traffic content (source/destination/ports),
            # not by rule name — a broader existing rule (e.g. dest '*') counts.
            cov_req = None
            if dests and fw_action in ("add", "modify") and not perrors:
                cov_req = {"kind": rule_kind, "sources": sources, "dests": dests,
                           "ports": ports, "ip_protocols": ip_protocols,
                           "app_protocols": app_protocols}
            res = azure_tools.get_firewall_policy_status(rule_name=rule_name, coverage=cov_req)
            res["rule_name"] = rule_name
            res["fw_action"] = fw_action
            if perrors:
                res["param_errors"] = perrors
            _audit_azure(res)
            return jsonify(res), (200 if res.get("success") else 207)

        if action == "fw_ensure_policy":
            fc = None
            if rcg_sel and col_sel:
                fc = FwCollection.query.filter_by(rcg=rcg_sel, collection=col_sel).first()
            res = azure_tools.ensure_firewall_policy(
                rcg_name=rcg_sel or None, collection_name=col_sel or None,
                rcg_priority=fc.priority if fc else 200,
                collection_priority=fc.priority if fc else 200,
                action=fc.action if fc else "Allow")
            _audit_azure(res)
            return jsonify(res), (200 if res.get("success") else 207)

        on_conflict = str(body.get("on_conflict", "")).strip()

        # fw_apply — perform the requested add / modify / delete
        if perrors and fw_action in ("add", "modify"):
            return jsonify({"error": "Fix the request's ports/protocol first: "
                                     + "; ".join(perrors)}), 400
        if fw_action in ("add", "modify") and not dests:
            return jsonify({"error": "Request has no destination in its details."}), 400
        if fw_action in ("add", "modify") and (not rcg_sel or not col_sel):
            return jsonify({"error": "Select a rule collection group and rule collection "
                                     "before applying."}), 400
        if fw_action in ("add", "modify") and rule_kind == "application":
            bad = _fqdn_errors(dests)
            if bad:
                return jsonify({"error": "Application rules only accept FQDN destinations "
                                         "(e.g. *.example.com) — these are IPs: "
                                         + ", ".join(bad) + ". Use a network rule instead."}), 400
        if fw_action == "delete":
            res = azure_tools.remove_firewall_rule(
                rule_name, rcg_name=rcg_sel or None, collection_name=col_sel or None)
        elif fw_action == "modify":
            res = azure_tools.replace_firewall_rule(
                rule_name, rule_kind, sources, dests,
                ports=ports, ip_protocols=ip_protocols, app_protocols=app_protocols,
                rcg_name=rcg_sel or None, collection_name=col_sel or None)
        else:  # add — with view/edit/proceed handling when the rule already exists
            if on_conflict == "keep":
                res = {"success": True, "kept_existing": True,
                       "message": f"Existing rule '{rule_name}' kept as-is — request accepted "
                                  f"without Azure changes."}
            elif on_conflict == "replace":
                res = azure_tools.replace_firewall_rule(
                    rule_name, rule_kind, sources, dests,
                    ports=ports, ip_protocols=ip_protocols, app_protocols=app_protocols,
                    rcg_name=rcg_sel or None, collection_name=col_sel or None)
            elif rule_kind == "application":
                res = azure_tools.add_firewall_application_rule(
                    rule_name, dests, app_protocols, source_addresses=sources,
                    rcg_name=rcg_sel, collection_name=col_sel)
            else:
                res = azure_tools.add_firewall_network_rule(
                    rule_name, dests, ports, protocol=ip_protocols, source_addresses=sources,
                    rcg_name=rcg_sel, collection_name=col_sel)
        if res.get("success") and req.status in (RequestStatus.SUBMITTED, RequestStatus.IN_REVIEW,
                                                 RequestStatus.RULE_IMPLEMENTED):
            # The requested policy change is applied — close the request and
            # notify the requester right away.
            req.status = RequestStatus.COMPLETED
            req.updated_at = datetime.utcnow()
            db.session.commit()
            notified = False
            try:
                notified = bool(notifications.notify_status_changed(req))
            except Exception:
                pass
            res["message"] = (str(res.get("message", ""))
                              + " Request completed"
                              + (" — requester notified." if notified else "."))
        _audit_azure(res)
        return jsonify(res), (200 if res.get("success") else 207)

    # ── NMO ZPA routing actions — driven by request details ──
    if action.startswith("nmo_"):
        spoke_cidr = details.get("spoke_cidr", "")
        if not spoke_cidr:
            return jsonify({"error": "Request has no spoke CIDR in its details."}), 400

        if action == "nmo_zpa_route":
            if not cfg.UDR_ZPA_NMO_NAME:
                return jsonify({"error": "ZPA NMO route table not configured "
                                         "(Settings → ZPA NMO Integration)."}), 400
            res = _route_res(
                route_table_name=cfg.UDR_ZPA_NMO_NAME, resource_group=cfg.UDR_RESOURCE_GROUP,
                route_name=render_name("TPL_ROUTE_NAME",
                                       vnet=details.get("spoke_vnet_name", f"req{req.id}"),
                                       request_id=req.id),
                address_prefix=spoke_cidr, next_hop_type="VirtualAppliance",
                next_hop_ip=cfg.HUB_FIREWALL_PRIVATE_IP, subscription_id=cfg.HUB_SUBSCRIPTION_ID)
        elif action == "nmo_spoke_udr":
            if not cfg.ZPA_NMO_CONNECTION_SUBNET:
                return jsonify({"error": "NMO connector subnet not configured "
                                         "(Settings → ZPA NMO Integration)."}), 400
            udr_name = details.get("spoke_udr_name", "")
            udr_rg = details.get("spoke_udr_rg", "")
            if not udr_name or not udr_rg:
                return jsonify({"error": "Request details are missing the spoke UDR "
                                         "name/resource group."}), 400
            res = _route_res(
                route_table_name=udr_name, resource_group=udr_rg,
                route_name=sanitize("to-zpa-nmo"),
                address_prefix=cfg.ZPA_NMO_CONNECTION_SUBNET, next_hop_type="VirtualAppliance",
                next_hop_ip=cfg.HUB_FIREWALL_PRIVATE_IP,
                subscription_id=details.get("spoke_subscription_id") or cfg.SPOKE_SUBSCRIPTION_ID)
        elif action == "nmo_nsg_check":
            res = azure_tools.get_nsg_rule_status(cfg.NMO_NSG_NAME, cfg.NMO_NSG_RG,
                                                  cfg.NMO_NSG_ALLOW_RULE)
        elif action == "nmo_nsg_add":
            if not cfg.NMO_NSG_NAME or not cfg.NMO_NSG_RG or not cfg.NMO_NSG_ALLOW_RULE:
                return jsonify({"error": "NMO NSG name/resource group/rule not configured "
                                         "(Settings → ZPA NMO Integration)."}), 400
            res = azure_tools.add_cidr_to_nsg_rule(cfg.NMO_NSG_NAME, cfg.NMO_NSG_RG,
                                                   cfg.NMO_NSG_ALLOW_RULE, spoke_cidr)
        elif action in ("nmo_fw_allow_check", "nmo_fw_deny_check"):
            rule = cfg.NMO_FW_ALLOW_RULE if action == "nmo_fw_allow_check" else cfg.NMO_FW_DENY_RULE
            if not rule:
                return jsonify({"error": "NMO firewall rule name not configured "
                                         "(Settings → ZPA NMO Integration)."}), 400
            res = azure_tools.get_firewall_policy_status(rule_name=rule)
            res["rule_name"] = rule
        elif action in ("nmo_fw_allow_add", "nmo_fw_deny_add"):
            rule = cfg.NMO_FW_ALLOW_RULE if action == "nmo_fw_allow_add" else cfg.NMO_FW_DENY_RULE
            if not rule:
                return jsonify({"error": "NMO firewall rule name not configured "
                                         "(Settings → ZPA NMO Integration)."}), 400
            res = azure_tools.add_cidr_to_firewall_rule(rule, spoke_cidr)
        else:
            return jsonify({"error": f"Unknown action '{action}'."}), 400
        _audit_azure(res)
        _auto_advance(req)
        return jsonify(res), (200 if res.get("success") else 207)

    # ── DNS actions (all three kinds share the fetch → act → complete flow) ──
    if action in ("dns_check", "dns_apply"):
        kind = details.get("dns_kind", "record_add")
        zone = details.get("zone", "")
        rtype = details.get("record_type", "A")
        rname = details.get("record_name", "")
        rvalue = details.get("record_value", "")
        if not zone:
            return jsonify({"error": "Request details are missing the DNS zone."}), 400
        if kind == "record_add" and not rname:
            return jsonify({"error": "Request details are missing the record name."}), 400

        if action == "dns_check":
            if kind == "record_add":
                res = azure_tools.get_dns_record_status(zone, rtype, rname)
            elif kind == "hub_zone_link_to_vnet":
                res = azure_tools.get_dns_zone_link_status(zone, vnet_name=details.get("vnet_name"))
            else:                                     # zone_link_to_hub
                res = azure_tools.get_dns_zone_link_status(zone)
            _audit_azure(res)
            return jsonify(res), (200 if res.get("success") else 207)

        # dns_apply — perform the kind's operation (with view/edit-or-reject conflicts)
        if kind == "record_add":
            res = azure_tools.upsert_dns_record(zone, rtype, rname, rvalue,
                                                on_conflict=(on_conflict or None))
        elif kind == "zone_link_to_hub":
            if on_conflict == "keep":
                res = {"success": True, "kept_existing": True,
                       "message": f"Zone '{zone}' already exists in the hub — request marked "
                                  f"complete without Azure changes."}
            else:
                res = azure_tools.create_dns_zone_in_hub(zone)
        elif kind == "hub_zone_link_to_vnet":
            missing = [k for k in ("subscription_id", "resource_group", "vnet_name")
                       if not details.get(k)]
            if missing:
                return jsonify({"error": "Request details are missing: " + ", ".join(missing)}), 400
            if on_conflict == "keep":
                res = {"success": True, "kept_existing": True,
                       "message": f"Existing link on '{zone}' kept as-is — request marked "
                                  f"complete without Azure changes."}
            else:
                res = azure_tools.link_dns_zone_to_vnet(
                    zone, details["subscription_id"], details["resource_group"],
                    details["vnet_name"], on_conflict=(on_conflict or None))
        else:
            return jsonify({"error": f"Unknown DNS request kind '{kind}'."}), 400
        if res.get("success") and req.status in (RequestStatus.SUBMITTED,
                                                 RequestStatus.IN_PROGRESS):
            req.status = RequestStatus.COMPLETED
            req.updated_at = datetime.utcnow()
            db.session.commit()
            notified = False
            try:
                notified = bool(notifications.notify_status_changed(req))
            except Exception:
                pass
            res["message"] = (str(res.get("message", "")) + " Request completed"
                              + (" — requester notified." if notified else "."))
        _audit_azure(res)
        _auto_advance(req)
        return jsonify(res), (200 if res.get("success") else 207)

    # ── AKS cluster actions — read-only status poll + non-blocking deploy ──
    if action in ("aks_check", "aks_deploy", "aks_link_dns"):
        sub  = (details.get("subscription_id")
                or cfg.SPOKE_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID)
        rg   = details.get("resource_group", "")
        name = details.get("cluster_name", "")
        if not rg or not name:
            return jsonify({"error": "Request details are missing the cluster name / resource group."}), 400

        def _maybe_complete_aks(res):
            """Complete the request when the cluster is Succeeded and every required
            AKS step (deploy, and the DNS link when ZPA access was requested) is done."""
            if (req.status not in RequestType.TERMINALS and req.status != RequestStatus.COMPLETED
                    and _required_actions(req) <= _done_actions(req)):
                req.status = RequestStatus.COMPLETED
                req.updated_at = datetime.utcnow()
                db.session.commit()
                try:
                    notifications.notify_status_changed(req)
                except Exception:
                    pass
                res["message"] = str(res.get("message", "")) + " — request completed."
                res["completed"] = True

        if action == "aks_check":
            res = azure_tools.get_aks_cluster_status(sub, rg, name)
            if res.get("success") and res.get("provisioning_state") == "Succeeded":
                if "aks_link_dns" in _required_actions(req) and "aks_link_dns" not in _done_actions(req):
                    res["message"] = str(res.get("message", "")) + \
                        " — cluster ready. Next: run 'Link Private DNS Zone to Hub' to finish (ZPA R&D dependency)."
                else:
                    _maybe_complete_aks(res)
            res["already_completed"] = (req.status == RequestStatus.COMPLETED)
            _audit_azure(res)
            return jsonify(res), (200 if res.get("success") else 207)

        if action == "aks_link_dns":
            res = azure_tools.link_aks_private_dns_to_hub(sub, rg, name)
            _audit_azure(res)
            if res.get("success"):
                _maybe_complete_aks(res)
            return jsonify(res), (200 if res.get("success") else 207)

        # aks_deploy — kick off (or accept an already-existing) cluster
        vnet_sub = details.get("vnet_subscription_id") or sub
        vnet_rg  = details.get("vnet_resource_group") or rg
        vnet     = details.get("vnet_name", "")
        subnet   = details.get("subnet_name", "")
        if not vnet or not subnet:
            return jsonify({"error": "Request details are missing the VNET / subnet."}), 400
        subnet_id = (f"/subscriptions/{vnet_sub}/resourceGroups/{vnet_rg}"
                     f"/providers/Microsoft.Network/virtualNetworks/{vnet}/subnets/{subnet}")
        region = (payload.get("region") or details.get("region") or AKS_STANDARD_REGION)
        k8s_opts = list(AKS_FALLBACK_VERSIONS)
        size_opts = list(AKS_FALLBACK_SIZES)
        # Admin overrides from the deploy panel win over the request's stored values.
        def _ov(key, default=None):
            v = payload.get(key)
            if v not in (None, ""):
                return v
            dv = details.get(key)
            return dv if dv not in (None, "") else default
        k8s_version = _ov("k8s_version") or (k8s_opts[0] if k8s_opts else "")
        node_size   = _ov("node_size") or (size_opts[0] if size_opts else "Standard_D8ds_v5")
        tier        = _ov("tier") or cfg.AKS_DEFAULT_TIER or "Free"
        autoscaling = str(_ov("autoscaling", "disabled")).lower() == "enabled"

        def _int(v, d):
            try:
                return int(v)
            except (TypeError, ValueError):
                return d

        if on_conflict == "keep":
            res = {"success": True, "kept_existing": True,
                   "message": f"Existing AKS cluster '{name}' kept as-is — no Azure changes. "
                              f"Use 'Check Cluster State' to confirm it is ready."}
        else:
            res = azure_tools.create_aks_cluster(
                subscription_id=sub, resource_group=rg, cluster_name=name, location=region,
                subnet_id=subnet_id, kubernetes_version=k8s_version,
                node_pool_name=details.get("node_pool_name", "nodepool1"), node_size=node_size,
                tier=tier, zones=(_ov("zones") or cfg.AKS_DEFAULT_ZONES or "default"),
                autoscaling=autoscaling,
                node_count=_int(_ov("node_count"), cfg.AKS_DEFAULT_NODE_COUNT),
                min_count=_int(_ov("min_count"), cfg.AKS_DEFAULT_MIN_COUNT),
                max_count=_int(_ov("max_count"), cfg.AKS_DEFAULT_MAX_COUNT),
                on_conflict=("replace" if on_conflict == "replace" else None),
            )
        _audit_azure(res)
        _auto_advance(req)
        return jsonify(res), (200 if res.get("success") else 207)

    # ── VNET decommission actions — driven by request details ──
    if action in ("decom_check", "decom_execute", "decom_release"):
        vnet_name = details.get("vnet_name", "")
        rg        = details.get("resource_group", "")
        sub       = (details.get("subscription_id") or cfg.SPOKE_SUBSCRIPTION_ID
                     or cfg.HUB_SUBSCRIPTION_ID)
        cidr      = details.get("allocated_cidr") or req.allocated_subnet or ""
        if action in ("decom_check", "decom_execute") and not sub:
            return jsonify({"error": "No subscription ID — the request details don't include one and "
                                     "no default spoke/hub subscription is configured in Settings."}), 400

        if action == "decom_check":
            if not vnet_name or not rg:
                return jsonify({"error": "Request details are missing the VNET name/resource group."}), 400
            res = azure_tools.decommission_check(sub, rg, vnet_name)
            _audit_azure(res)
            return jsonify(res), (200 if res.get("success") else 207)

        if action == "decom_execute":
            if not vnet_name or not rg:
                return jsonify({"error": "Request details are missing the VNET name/resource group."}), 400
            steps = []

            def _step(label, res):
                _record_change(res)
                steps.append({"label": label, "success": bool(res.get("success")),
                              "dry_run": bool(res.get("dry_run")),
                              "message": str(res.get("message", ""))})
                return bool(res.get("success"))

            ok = _step("Delete hub ↔ spoke peerings",
                       azure_tools.delete_hub_spoke_peerings(sub, rg, vnet_name))
            if cidr:
                for table in filter(None, [(cfg.UDR_GATEWAY_NAME or cfg.UDR_NAME_1),
                                           (cfg.UDR_ZPA_NAME or cfg.UDR_NAME_2)]):
                    ok = _step(f"Remove spoke routes from '{table}'",
                               azure_tools.remove_routes_by_prefix(
                                   table, cfg.UDR_RESOURCE_GROUP, cidr,
                                   subscription_id=cfg.HUB_SUBSCRIPTION_ID)) and ok
            if cfg.FIREWALL_POLICY_NAME:
                rule_name = render_name("TPL_FW_RULE_NAME", vnet=vnet_name, request_id=req.id,
                                        cidr_mask=cidr.split("/")[-1] if cidr else "", purpose=req.purpose)
                ok = _step(f"Remove firewall rule '{rule_name}'",
                           azure_tools.remove_firewall_rule(rule_name)) and ok
            ok = _step(f"Delete VNET '{vnet_name}'",
                       azure_tools.delete_spoke_vnet(sub, rg, vnet_name)) and ok

            if ok and req.status in (RequestStatus.SUBMITTED, RequestStatus.IN_PROGRESS):
                req.status = RequestStatus.RESOURCES_REMOVED
                req.updated_at = datetime.utcnow()
                db.session.commit()
            res = {"success": ok, "steps": steps,
                   "message": ("Azure resources removed — status set to Resources Removed."
                               if ok else "Some decommission steps failed — see details below.")}
            _audit_azure(res)
            return jsonify(res), (200 if ok else 207)

        # decom_release — app-side only: release the CIDR from the inventory
        if not cidr:
            return jsonify({"error": "No CIDR to release — the request has no allocated/declared CIDR."}), 400
        from db_utils import deallocate_subnet_db
        rec = SubnetRecord.query.filter_by(subnet=cidr).first()
        if rec:
            changes.record(action="decom_release", actor=current_actor(), request_id=req.id,
                           target=f"CIDR {cidr}", summary=f"Released {cidr} during decommission",
                           before={"subnet": rec.subnet, "pool": rec.pool,
                                   "purpose": rec.purpose or "",
                                   "requested_by": rec.requested_by or "",
                                   "allocated_by": rec.allocated_by or ""},
                           after=None, revert_op="allocate_cidr",
                           revert_params={"subnet": rec.subnet, "pool": rec.pool,
                                          "purpose": rec.purpose or "",
                                          "requested_by": rec.requested_by or "",
                                          "allocated_by": rec.allocated_by or ""})
        ok, msg = deallocate_subnet_db(cidr)
        already_gone = not ok and "not found" in str(msg).lower()
        if ok or already_gone:
            req.allocated_subnet = None
            if req.status in (RequestStatus.SUBMITTED, RequestStatus.IN_PROGRESS,
                              RequestStatus.RESOURCES_REMOVED):
                req.status = RequestStatus.CIDR_RELEASED
            req.updated_at = datetime.utcnow()
            db.session.commit()
            res = {"success": True,
                   "message": (f"CIDR {cidr} released from the app inventory."
                               if ok else f"CIDR {cidr} was not in the inventory — assignment cleared anyway.")}
        else:
            res = {"success": False, "message": msg}
        audit.record("cidr_deallocated" if ok else "azure_action", actor=current_actor(),
                     actor_role="admin", request_id=req.id,
                     summary=f"Decommission release — {res['message'][:200]}",
                     data={"action": action, "subnet": cidr, "success": res["success"]})
        return jsonify(res), (200 if res.get("success") else 207)

    addr = req.allocated_subnet
    if not addr:
        return jsonify({"error": "No CIDR has been assigned yet."}), 400
    vi = req.vnet_info
    if not vi or not all([vi.subscription_id, vi.resource_group, vi.vnet_name, vi.region]):
        return jsonify({"error": "VNET info incomplete (Subscription ID, Resource Group, "
                                 "VNET Name, Region). Edit VNET Info first."}), 400

    if action == "vnet":
        if on_conflict == "keep":
            res = {"success": True, "kept_existing": True,
                   "message": f"Existing VNET '{vi.vnet_name}' kept as-is — deploy step "
                              f"marked complete without Azure changes."}
        else:
            res = azure_tools.create_spoke_vnet(vi.subscription_id, vi.resource_group,
                                                vi.vnet_name, vi.region, addr,
                                                subnet_name=vi.subnet_name or "default",
                                                subnet_size=vi.subnet_size,
                                                subnets=details.get("subnets") or None,
                                                on_conflict=(on_conflict or None))
        if res.get("success") and req.status == RequestStatus.CIDR_ASSIGNED:
            req.status = RequestStatus.VNET_CREATED
            req.updated_at = datetime.utcnow()
            db.session.commit()
            try:
                notifications.notify_vnet_created(req)
            except Exception:
                pass
    elif action == "peer":
        body = request.get_json(force=True) or {}
        raw_s2h = str(body.get("spoke_to_hub_name", "")).strip()
        raw_h2s = str(body.get("hub_to_spoke_name", "")).strip()
        s2h = sanitize(raw_s2h) if raw_s2h else None
        h2s = sanitize(raw_h2s) if raw_h2s else None
        if on_conflict == "keep":
            res = {"success": True, "kept_existing": True,
                   "message": "Existing peering kept as-is — peer step marked complete "
                              "without Azure changes."}
        else:
            res = azure_tools.peer_hub_vnet(spoke_subscription_id=vi.subscription_id,
                                            spoke_resource_group=vi.resource_group,
                                            spoke_vnet_name=vi.vnet_name, spoke_address_space=addr,
                                            spoke_to_hub_name=s2h, hub_to_spoke_name=h2s,
                                            on_conflict=(on_conflict or None))
        if res.get("success"):
            # Remember the names actually used — revert/decommission delete by them
            d = req.get_details()
            d["peering_names"] = {
                "spoke_to_hub": res.get("spoke_to_hub_name") or s2h
                                or render_name("TPL_PEERING_SPOKE_TO_HUB", vnet=vi.vnet_name),
                "hub_to_spoke": res.get("hub_to_spoke_name") or h2s
                                or render_name("TPL_PEERING_HUB_TO_SPOKE", vnet=vi.vnet_name),
            }
            req.set_details(d)
            db.session.commit()
    elif action == "internet_rule":
        # Requester asked for a specific network/application internet rule, not allow-all
        ia = details.get("internet_access", "")
        if ia not in ("network", "application"):
            return jsonify({"error": "This request did not ask for a specific internet rule."}), 400
        body = request.get_json(force=True) or {}
        rcg_sel = str(body.get("rcg", "")).strip()
        col_sel = str(body.get("collection", "")).strip()
        if not rcg_sel or not col_sel:
            return jsonify({"error": "Select a rule collection group and rule collection "
                                     "before applying."}), 400
        _s, dests, ip_protocols, ports, app_protocols, perrors = _fw_params(
            {"source": addr, "destination": details.get("internet_dest", ""),
             "ports_protocol": details.get("internet_ports", "")})
        if perrors:
            return jsonify({"error": "Fix the request's ports/protocol first: "
                                     + "; ".join(perrors)}), 400
        if not dests:
            return jsonify({"error": "The request has no internet destinations in its details."}), 400
        rule_name = sanitize(f"{vi.vnet_name}-inet-req{req.id}")
        on_conflict = str(body.get("on_conflict", "")).strip()
        if ia == "application":
            bad = _fqdn_errors(dests)
            if bad:
                return jsonify({"error": "Application rules only accept FQDN destinations — "
                                         "these are IPs: " + ", ".join(bad)}), 400
        if on_conflict == "keep":
            res = {"success": True, "kept_existing": True,
                   "message": f"Existing firewall rule '{rule_name}' kept as-is — internet step "
                              f"marked complete without Azure changes."}
        elif on_conflict == "replace":
            res = azure_tools.replace_firewall_rule(
                rule_name, ia, [addr], dests, ports=ports, ip_protocols=ip_protocols,
                app_protocols=app_protocols, rcg_name=rcg_sel or None,
                collection_name=col_sel or None)
        elif ia == "application":
            res = azure_tools.add_firewall_application_rule(
                rule_name, dests, app_protocols, source_addresses=[addr],
                rcg_name=rcg_sel, collection_name=col_sel)
        else:
            res = azure_tools.add_firewall_network_rule(
                rule_name, dests, ports, protocol=ip_protocols, source_addresses=[addr],
                rcg_name=rcg_sel, collection_name=col_sel)
    elif action == "spoke_route_table":
        # Create the spoke route table (default + additional routes) and assign
        # it to every workload subnet of the spoke VNET.
        body = request.get_json(force=True) or {}
        extra, bad = [], []
        for r in (body.get("additional_routes") or []):
            name = sanitize(str(r.get("name", "")).strip() or "route")
            prefix = str(r.get("prefix", "")).strip()
            try:
                ipaddress.ip_network(prefix, strict=False)
                extra.append({"name": name, "prefix": prefix})
            except ValueError:
                bad.append(prefix or "(empty)")
        if bad:
            return jsonify({"error": "Invalid additional route prefix(es): " + ", ".join(bad)}), 400

        rt_name = render_name("TPL_ROUTE_TABLE_NAME", vnet=vi.vnet_name, request_id=req.id)
        steps = []

        def _step(label, r):
            _record_change(r)
            steps.append({"label": label, "success": bool(r.get("success")),
                          "dry_run": bool(r.get("dry_run")),
                          "message": str(r.get("message", ""))})
            return bool(r.get("success"))

        create_res = azure_tools.create_route_table(
            rt_name, vi.resource_group, location=vi.region,
            subscription_id=vi.subscription_id,
            on_conflict=("keep" if on_conflict == "keep" else None))
        if create_res.get("conflict"):
            # Existing table with routes — never overwrite silently; the UI
            # shows its current routes and offers reuse-as-is.
            return jsonify(create_res), 207
        ok = _step(f"Create route table '{rt_name}'", create_res)
        for r in _spoke_default_routes() + extra:
            ok = _step(f"Route {r['name']} → {r['prefix']} via {cfg.HUB_FIREWALL_PRIVATE_IP}",
                       azure_tools.add_route_to_table(
                           rt_name, vi.resource_group, r["name"], r["prefix"],
                           "VirtualAppliance", next_hop_ip=cfg.HUB_FIREWALL_PRIVATE_IP,
                           subscription_id=vi.subscription_id)) and ok
        rt_id = (f"/subscriptions/{vi.subscription_id}/resourceGroups/{vi.resource_group}"
                 f"/providers/Microsoft.Network/routeTables/{rt_name}")
        skip = ("GatewaySubnet", "AzureFirewallSubnet", "AzureFirewallManagementSubnet",
                "AzureBastionSubnet")
        # Admin can pick exactly which subnets get the route table; when the
        # picker sent a selection, honour it verbatim (minus system subnets).
        selected = [str(s).strip() for s in (body.get("assign_subnets") or []) if str(s).strip()]
        if "assign_subnets" in body and not selected:
            return jsonify({"error": "Select at least one subnet to assign the route table to."}), 400
        if selected:
            targets = [s for s in selected if s not in skip]
        else:
            listing = azure_tools.list_vnet_subnets(vi.subscription_id, vi.resource_group, vi.vnet_name)
            if listing.get("success"):
                targets = [s["name"] for s in listing["subnets"] if s["name"] not in skip]
            else:
                # VNET not reachable (e.g. dry-run before deploy) — use the declared subnets
                targets = [s.get("name") for s in (details.get("subnets") or []) if s.get("name")] \
                          or ([vi.subnet_name] if vi.subnet_name else [])
                steps.append({"label": "List spoke subnets", "success": bool(targets), "dry_run": False,
                              "message": (f"Could not list live subnets ({listing.get('message', '')[:120]}) — "
                                          f"using the request's declared subnet(s).") if targets else
                                         "Could not list subnets and none are declared on the request."})
                ok = ok and bool(targets)
        for name in targets:
            ok = _step(f"Assign route table to subnet '{name}'",
                       azure_tools.assign_route_table_to_subnet(
                           vi.subscription_id, vi.resource_group, vi.vnet_name,
                           name, rt_id)) and ok
        res = {"success": ok, "steps": steps,
               "message": (f"Route table '{rt_name}' created and assigned to "
                           f"{len(targets)} subnet(s)." if ok else
                           "Some route-table steps failed — see details.")}
    elif action == "internet":
        body = request.get_json(force=True) or {}
        rule_name = render_name("TPL_FW_RULE_NAME", vnet=vi.vnet_name, request_id=req.id,
                                region=vi.region, cidr_mask=addr.split("/")[-1], purpose=req.purpose)
        on_conflict = str(body.get("on_conflict", "")).strip()
        if on_conflict == "keep":
            res = {"success": True, "kept_existing": True,
                   "message": f"Existing firewall rule '{rule_name}' kept as-is — internet step "
                              f"marked complete without Azure changes."}
        elif on_conflict == "replace":
            res = azure_tools.replace_firewall_rule(
                rule_name, "network", [addr], ["*"], ports=["*"], ip_protocols=["Any"])
        else:
            res = azure_tools.allow_internet_rule(addr, rule_name)
    elif action in ("gateway_route", "zpa_route"):
        table = (cfg.UDR_GATEWAY_NAME or cfg.UDR_NAME_1) if action == "gateway_route" \
                else (cfg.UDR_ZPA_NAME or cfg.UDR_NAME_2)
        if not table:
            return jsonify({"error": f"No routing table configured for {action} "
                                     f"(set UDR_GATEWAY_NAME / UDR_ZPA_NAME)."}), 400
        route_name = render_name("TPL_ROUTE_NAME", vnet=vi.vnet_name, request_id=req.id,
                                 region=vi.region, cidr_mask=addr.split("/")[-1], purpose=req.purpose)
        res = _route_res(
            route_table_name=table, resource_group=cfg.UDR_RESOURCE_GROUP,
            route_name=route_name, address_prefix=addr,
            next_hop_type="VirtualAppliance", next_hop_ip=cfg.HUB_FIREWALL_PRIVATE_IP,
            subscription_id=cfg.HUB_SUBSCRIPTION_ID,
        )
    else:
        return jsonify({"error": f"Unknown action '{action}'."}), 400

    _record_change(res)
    audit.record("azure_action", actor=current_actor(), actor_role="admin", request_id=req.id,
                 summary=f"Azure action '{action}' — "
                         f"{'dry-run' if res.get('dry_run') else ('ok' if res.get('success') else 'FAILED')}",
                 data={"action": action, "dry_run": bool(res.get("dry_run")),
                       "success": bool(res.get("success")),
                       "kept": bool(res.get("kept_existing") or res.get("replaced_existing")),
                       "message": str(res.get("message", ""))[:400]})
    _auto_advance(req)
    return jsonify(res), (200 if res.get("success") else 207)


# ═══════════════════════════════════════════════════════════════════════════
# Cancel / Reject with automatic revert of deployed Azure changes
# ═══════════════════════════════════════════════════════════════════════════

# Deployed-change keys in revert order (most-dependent first; reverse of deploy)
_REVERT_ORDER = ["spoke_route_table", "spoke_udr_zpa", "zpa_route_to_spoke",
                 "nmo_fw_deny_add", "nmo_fw_allow_add", "nmo_nsg_add",
                 "nmo_spoke_udr", "nmo_zpa_route", "zpa_route",
                 "gateway_route", "internet", "internet_rule", "fw_apply", "peer", "vnet"]

_REVERT_LABELS = {
    "spoke_route_table": "Unassign & delete the spoke route table",
    "spoke_udr_zpa":     "Remove ZPA connection route from the spoke UDR",
    "zpa_route_to_spoke": "Remove spoke route from the ZPA connector routing table",
    "nmo_fw_deny_add":   "Remove spoke CIDR from the NMO firewall DENY rule",
    "nmo_fw_allow_add":  "Remove spoke CIDR from the NMO firewall ALLOW rule",
    "nmo_nsg_add":       "Remove spoke CIDR from the NMO NSG outbound allow rule",
    "nmo_spoke_udr":     "Remove NMO connector route from the spoke UDR",
    "nmo_zpa_route":     "Remove spoke route from the ZPA NMO routing table",
    "zpa_route":         "Remove spoke route from the hub ZPA routing table",
    "gateway_route":     "Remove spoke route from the hub gateway routing table",
    "internet":          "Remove the internet-egress firewall rule",
    "internet_rule":     "Remove the requested internet firewall rule",
    "fw_apply":          "Remove the firewall rule added by this request",
    "peer":              "Delete hub ↔ spoke VNET peerings (both directions)",
    "vnet":              "Delete the spoke VNET deployed by admin",
}


def _spoke_default_routes() -> list:
    """Parse the SPOKE_DEFAULT_ROUTES setting: 'name=prefix, name=prefix, …'."""
    import re
    from naming import sanitize
    out = []
    for part in re.split(r"[,;\n]", cfg.SPOKE_DEFAULT_ROUTES or ""):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, prefix = part.split("=", 1)
        name, prefix = sanitize(name.strip()), prefix.strip()
        try:
            ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            continue
        out.append({"name": name, "prefix": prefix})
    return out


def _done_actions(req) -> set:
    """Portal actions that have succeeded for this request (dry-run included),
    minus any that were later reverted. Drives button state + auto-status."""
    done = set()
    for e in reversed(audit.list_entries(request_id=req.id, limit=500)):
        d = e.get("data") or {}
        key = d.get("action")
        if not key or not d.get("success"):
            continue
        if e["action"] in ("azure_action", "cidr_deallocated"):
            done.add(key)
        elif e["action"] == "azure_revert":
            done.discard(key)
    return done


def _required_actions(req) -> set:
    """Portal actions that must succeed before the request auto-completes."""
    t = req.request_type or RequestType.VNET_NEW
    d = req.get_details()
    if t == RequestType.VNET_NEW and req.hub_integration:
        required = {"peer", "gateway_route", "zpa_route"}
        ia = d.get("internet_access") or "full"     # legacy requests = full internet
        if ia == "full":
            required.add("internet")
        elif ia in ("network", "application"):
            required.add("internet_rule")
        return required
    if t == RequestType.HUB_INTEGRATION:
        return {"peer", "gateway_route", "zpa_route"}
    if t in (RequestType.ZPA_RND_ROUTING, RequestType.ZPA_OTHER_ROUTING):
        return {"zpa_route_to_spoke", "spoke_udr_zpa"}
    if t == RequestType.ZPA_NMO_ROUTING:
        return {"nmo_zpa_route", "nmo_spoke_udr", "nmo_nsg_add",
                "nmo_fw_allow_add", "nmo_fw_deny_add"}
    if t == RequestType.AKS_CLUSTER:
        req_set = {"aks_deploy"}
        if str(d.get("zpa_rnd_access") or "").lower() == "yes":
            req_set.add("aks_link_dns")     # link the private DNS zone to the hub
        return req_set
    return set()


def _auto_advance(req):
    """
    Move the status forward based on what has actually been done via the
    portal — statuses are never switched manually anymore.
    """
    t = req.request_type or RequestType.VNET_NEW
    done = _done_actions(req)
    required = _required_actions(req)
    wf = req.workflow()
    new = None
    if t == RequestType.VNET_NEW and req.hub_integration:
        if required and required <= done:
            new = RequestStatus.HUB_INTEGRATED
        elif done & required and req.status == RequestStatus.VNET_CREATED:
            new = RequestStatus.HUB_INTEGRATION_IN_PROGRESS
    elif t == RequestType.HUB_INTEGRATION:
        if required <= done:
            new = RequestStatus.HUB_INTEGRATED
        elif done & required and req.status == RequestStatus.SUBMITTED:
            new = RequestStatus.HUB_INTEGRATION_IN_PROGRESS
    elif t in (RequestType.ZPA_RND_ROUTING, RequestType.ZPA_OTHER_ROUTING):
        if required <= done:
            new = RequestStatus.COMPLETED
        elif "spoke_udr_zpa" in done:
            new = RequestStatus.SPOKE_UDR_UPDATED
        elif "zpa_route_to_spoke" in done:
            new = RequestStatus.ZPA_ROUTE_ADDED
    elif t == RequestType.ZPA_NMO_ROUTING:
        if required <= done:
            new = RequestStatus.COMPLETED
        elif {"nmo_fw_allow_add", "nmo_fw_deny_add"} <= done:
            new = RequestStatus.FW_RULES_UPDATED
        elif "nmo_nsg_add" in done:
            new = RequestStatus.NSG_UPDATED
        elif "nmo_spoke_udr" in done:
            new = RequestStatus.SPOKE_UDR_UPDATED
        elif "nmo_zpa_route" in done:
            new = RequestStatus.ZPA_ROUTE_ADDED
    elif t == RequestType.AKS_CLUSTER:
        # Deploy kicks provisioning off → 'Cluster Deployed'. Completion happens
        # in the aks_check action once provisioningState is Succeeded.
        if "aks_deploy" in done:
            new = RequestStatus.AKS_DEPLOYED
    # Only ever move forward within the workflow
    if (not new or new == req.status or new not in wf
            or (req.status in wf and wf.index(new) <= wf.index(req.status))):
        return
    old = req.status
    req.status = new
    req.updated_at = datetime.utcnow()
    db.session.commit()
    audit.record("status_changed", actor="portal (auto)", actor_role="system", request_id=req.id,
                 summary=f"Status: {RequestStatus.label(old)} → {RequestStatus.label(new)} "
                         f"(automatic — driven by completed portal actions)",
                 data={"old": old, "new": new, "auto": True, "done": sorted(done)})
    try:
        if new == RequestStatus.HUB_INTEGRATION_IN_PROGRESS:
            notifications.notify_hub_in_progress(req)
        elif new == RequestStatus.HUB_INTEGRATED:
            notifications.notify_hub_integrated(req)
        else:
            notifications.notify_status_changed(req)
    except Exception:
        pass


def _deployed_changes(req):
    """
    What has actually been deployed for this request, derived from the audit
    trail: successful azure_action entries minus any later successful reverts.
    Returns a list (in revert order) plus a CIDR-release entry when applicable.
    """
    entries = audit.list_entries(request_id=req.id, limit=500)
    done = {}
    for e in reversed(entries):                      # chronological
        d = e.get("data") or {}
        key = d.get("action")
        if key not in _REVERT_ORDER or not d.get("success"):
            continue
        if e["action"] == "azure_action":
            if d.get("kept"):
                # Admin accepted a pre-existing rule as-is — this request
                # created nothing, so there is nothing to revert.
                done.pop(key, None)
                continue
            done[key] = {"dry_run": bool(d.get("dry_run"))}
        elif e["action"] == "azure_revert":
            done.pop(key, None)
    # A firewall 'add' can be reverted by removing the rule; an applied modify or
    # delete has no recorded previous state, so it isn't offered for auto-revert.
    if "fw_apply" in done and (req.get_details().get("action") or "add") != "add":
        done.pop("fw_apply")
    changes = [{"key": k, "label": _REVERT_LABELS[k], "dry_run": done[k]["dry_run"]}
               for k in _REVERT_ORDER if k in done]
    if req.allocated_subnet and (req.request_type or RequestType.VNET_NEW) == RequestType.VNET_NEW:
        changes.append({"key": "cidr", "dry_run": False,
                        "label": f"Release CIDR {req.allocated_subnet} back to the pool"})
    return changes


def _revert_change(req, key):
    """Undo one deployed change. Returns the azure_tools-style result dict."""
    import azure_tools
    from naming import sanitize
    vi = req.vnet_info
    details = req.get_details()
    addr = req.allocated_subnet or ""

    if key == "cidr":
        from db_utils import deallocate_subnet_db
        rec = SubnetRecord.query.filter_by(subnet=addr).first()
        snapshot = ({"subnet": rec.subnet, "pool": rec.pool, "purpose": rec.purpose or "",
                     "requested_by": rec.requested_by or "", "allocated_by": rec.allocated_by or ""}
                    if rec else None)
        ok, msg = deallocate_subnet_db(addr)
        if ok or "not found" in str(msg).lower():
            req.allocated_subnet = None
            db.session.commit()
            out = {"success": True, "message": f"CIDR {addr} released back to the pool."}
            if snapshot:
                out["change"] = {"target": f"CIDR {addr}", "before": snapshot, "after": None,
                                 "revert_op": "allocate_cidr", "revert_params": snapshot}
            return out
        return {"success": False, "message": msg}

    if key in ("vnet", "peer", "internet", "internet_rule", "gateway_route",
               "zpa_route", "spoke_route_table"):
        if not vi or not all([vi.subscription_id, vi.resource_group, vi.vnet_name]):
            return {"success": False, "message": "VNET info missing — cannot compute what to revert."}

    if key == "vnet":
        return azure_tools.delete_spoke_vnet(vi.subscription_id, vi.resource_group, vi.vnet_name)
    if key == "peer":
        pn = details.get("peering_names") or {}
        return azure_tools.delete_hub_spoke_peerings(
            vi.subscription_id, vi.resource_group, vi.vnet_name,
            spoke_to_hub_name=pn.get("spoke_to_hub"), hub_to_spoke_name=pn.get("hub_to_spoke"))
    if key == "internet":
        rule_name = render_name("TPL_FW_RULE_NAME", vnet=vi.vnet_name, request_id=req.id,
                                region=vi.region, cidr_mask=addr.split("/")[-1], purpose=req.purpose)
        return azure_tools.remove_firewall_rule(rule_name)
    if key == "internet_rule":
        return azure_tools.remove_firewall_rule(sanitize(f"{vi.vnet_name}-inet-req{req.id}"))
    if key == "spoke_route_table":
        return azure_tools.delete_spoke_route_table(
            vi.subscription_id, vi.resource_group, vi.vnet_name,
            render_name("TPL_ROUTE_TABLE_NAME", vnet=vi.vnet_name, request_id=req.id))
    if key in ("gateway_route", "zpa_route"):
        table = (cfg.UDR_GATEWAY_NAME or cfg.UDR_NAME_1) if key == "gateway_route" \
                else (cfg.UDR_ZPA_NAME or cfg.UDR_NAME_2)
        if not table:
            return {"success": False, "message": f"No routing table configured for {key}."}
        route_name = render_name("TPL_ROUTE_NAME", vnet=vi.vnet_name, request_id=req.id,
                                 region=vi.region, cidr_mask=addr.split("/")[-1], purpose=req.purpose)
        return azure_tools.delete_route_from_table(table, cfg.UDR_RESOURCE_GROUP, route_name,
                                                   subscription_id=cfg.HUB_SUBSCRIPTION_ID)
    if key == "zpa_route_to_spoke":
        table = details.get("connector_route_table") or cfg.UDR_ZPA_NAME
        if not table:
            return {"success": False, "message": "No ZPA routing table configured."}
        route_name = render_name("TPL_ROUTE_NAME",
                                 vnet=details.get("spoke_vnet_name", f"req{req.id}"), request_id=req.id)
        return azure_tools.delete_route_from_table(table, cfg.UDR_RESOURCE_GROUP, route_name,
                                                   subscription_id=cfg.HUB_SUBSCRIPTION_ID)
    if key == "fw_apply":
        rule_kind = (details.get("rule_kind") or "network").lower()
        rule_name = (details.get("rule_name") or "").strip() \
                    or sanitize(f"req-{req.id}-{rule_kind}-rule")
        return azure_tools.remove_firewall_rule(rule_name)
    if key == "spoke_udr_zpa":
        udr_name, udr_rg = details.get("spoke_udr_name", ""), details.get("spoke_udr_rg", "")
        if not udr_name or not udr_rg:
            return {"success": False, "message": "Spoke UDR name/resource group missing from details."}
        return azure_tools.delete_route_from_table(
            udr_name, udr_rg, sanitize(f"to-zpa-{details.get('connector_name', 'rnd')}"),
            subscription_id=details.get("spoke_subscription_id") or cfg.SPOKE_SUBSCRIPTION_ID)
    if key == "nmo_zpa_route":
        return azure_tools.delete_route_from_table(
            cfg.UDR_ZPA_NMO_NAME, cfg.UDR_RESOURCE_GROUP,
            render_name("TPL_ROUTE_NAME", vnet=details.get("spoke_vnet_name", f"req{req.id}"),
                        request_id=req.id),
            subscription_id=cfg.HUB_SUBSCRIPTION_ID)
    if key == "nmo_spoke_udr":
        udr_name, udr_rg = details.get("spoke_udr_name", ""), details.get("spoke_udr_rg", "")
        if not udr_name or not udr_rg:
            return {"success": False, "message": "Spoke UDR name/resource group missing from details."}
        return azure_tools.delete_route_from_table(
            udr_name, udr_rg, sanitize("to-zpa-nmo"),
            subscription_id=details.get("spoke_subscription_id") or cfg.SPOKE_SUBSCRIPTION_ID)
    if key == "nmo_nsg_add":
        return azure_tools.remove_cidr_from_nsg_rule(
            cfg.NMO_NSG_NAME, cfg.NMO_NSG_RG, cfg.NMO_NSG_ALLOW_RULE,
            details.get("spoke_cidr", ""))
    if key in ("nmo_fw_allow_add", "nmo_fw_deny_add"):
        rule = cfg.NMO_FW_ALLOW_RULE if key == "nmo_fw_allow_add" else cfg.NMO_FW_DENY_RULE
        return azure_tools.remove_cidr_from_firewall_rule(rule, details.get("spoke_cidr", ""))
    return {"success": False, "message": f"Unknown change '{key}'."}


@app.route("/api/admin/requests/<int:req_id>/spoke-subnets")
@require_admin
def request_spoke_subnets(req_id):
    """Subnets of the request's spoke VNET — live from Azure when reachable,
    otherwise the subnets declared on the request. Feeds the UDR-assignment picker."""
    import azure_tools
    req = SpokeRequest.query.get_or_404(req_id)
    vi = req.vnet_info
    skip = ("GatewaySubnet", "AzureFirewallSubnet", "AzureFirewallManagementSubnet",
            "AzureBastionSubnet")
    if vi and all([vi.subscription_id, vi.resource_group, vi.vnet_name]):
        listing = azure_tools.list_vnet_subnets(vi.subscription_id, vi.resource_group, vi.vnet_name)
        if listing.get("success"):
            return jsonify({"source": "azure", "subnets": [
                {"name": s["name"], "prefix": s.get("address_prefix") or "",
                 "has_udr": bool(s.get("has_udr"))}
                for s in listing["subnets"] if s["name"] not in skip]})
    declared = [{"name": s.get("name"), "prefix": "", "has_udr": False}
                for s in (req.get_details().get("subnets") or []) if s.get("name")]
    if not declared and vi and vi.subnet_name:
        declared = [{"name": vi.subnet_name, "prefix": "", "has_udr": False}]
    return jsonify({"source": "declared", "subnets": declared})


# Creation order (the reverse of teardown): VNET first, then peering/rules/hub
# routes, and finally the spoke route table (needs the subnets to exist).
_DEPLOY_ORDER = ["vnet", "peer", "internet", "internet_rule", "gateway_route",
                 "zpa_route", "spoke_route_table"]
_DEPLOY_LABELS = {
    "vnet":              "Create the spoke VNET & subnet(s)",
    "peer":              "Peer the spoke VNET with the hub (both directions)",
    "internet":          "Add the internet-egress firewall rule (allow-all)",
    "internet_rule":     "Add the requested internet firewall rule",
    "gateway_route":     "Add the spoke route to the hub gateway routing table",
    "zpa_route":         "Add the spoke route to the hub ZPA routing table",
    "spoke_route_table": "Create the spoke route table (UDR) & assign it to the VNET's subnets",
}


def _pending_deploy_actions(req):
    """Ordered list of required deploy steps not yet done, for the aggregated
    deploy. internet_rule is flagged 'manual' — it needs a human to choose the
    rule collection group & collection, so the one-click deploy can't run it."""
    done = _done_actions(req)
    required = set(_required_actions(req))
    if (req.request_type or RequestType.VNET_NEW) == RequestType.VNET_NEW \
            and req.deployment_mode == "admin":
        # Admin-deploy of a new spoke: create the VNET and lay its UDR/route table.
        required.add("vnet")
        required.add("spoke_route_table")
    plan = []
    for k in _DEPLOY_ORDER:
        if k in required and k not in done:
            plan.append({"key": k, "label": _DEPLOY_LABELS[k], "manual": k == "internet_rule"})
    return plan


def _deploy_spoke_route_table(req):
    """Create the spoke route table (default + hub-firewall routes) and assign it
    to every workload subnet of the spoke VNET. Records each sub-change to the
    ledger under the 'spoke_route_table' key so it reverts as one unit."""
    import azure_tools
    vi = req.vnet_info
    details = req.get_details()
    rt_name = render_name("TPL_ROUTE_TABLE_NAME", vnet=vi.vnet_name, request_id=req.id)

    def _rec(res):
        ch = res.get("change")
        if res.get("success") and ch and not res.get("dry_run"):
            changes.record(action="spoke_route_table", actor=current_actor(), request_id=req.id,
                           target=ch.get("target", ""), summary=str(res.get("message", ""))[:300],
                           before=ch.get("before"), after=ch.get("after"),
                           revert_op=ch.get("revert_op"), revert_params=ch.get("revert_params"))

    create_res = azure_tools.create_route_table(rt_name, vi.resource_group, location=vi.region,
                                                 subscription_id=vi.subscription_id)
    if create_res.get("conflict"):
        return {"success": False, "conflict": True, "dry_run": False,
                "message": f"Route table '{rt_name}' already exists — reuse/resolve it via the "
                           f"individual 'Spoke Route Table' step."}
    _rec(create_res)
    ok = bool(create_res.get("success"))
    for r in _spoke_default_routes():
        rr = azure_tools.add_route_to_table(
            rt_name, vi.resource_group, r["name"], r["prefix"], "VirtualAppliance",
            next_hop_ip=cfg.HUB_FIREWALL_PRIVATE_IP, subscription_id=vi.subscription_id)
        _rec(rr)
        ok = ok and bool(rr.get("success"))
    rt_id = (f"/subscriptions/{vi.subscription_id}/resourceGroups/{vi.resource_group}"
             f"/providers/Microsoft.Network/routeTables/{rt_name}")
    skip = ("GatewaySubnet", "AzureFirewallSubnet", "AzureFirewallManagementSubnet", "AzureBastionSubnet")
    listing = azure_tools.list_vnet_subnets(vi.subscription_id, vi.resource_group, vi.vnet_name)
    if listing.get("success"):
        targets = [s["name"] for s in listing["subnets"] if s["name"] not in skip]
    else:                                    # VNET not reachable (e.g. dry-run) — use declared subnets
        targets = [s.get("name") for s in (details.get("subnets") or []) if s.get("name")] \
                  or ([vi.subnet_name] if vi.subnet_name else [])
    for name in targets:
        ar = azure_tools.assign_route_table_to_subnet(
            vi.subscription_id, vi.resource_group, vi.vnet_name, name, rt_id)
        _rec(ar)
        ok = ok and bool(ar.get("success"))
    return {"success": ok, "dry_run": bool(create_res.get("dry_run")),
            "message": (f"Route table '{rt_name}' created & assigned to {len(targets)} subnet(s)."
                        if ok else "Some route-table steps failed — check the individual step.")}


def _deploy_one(req, key):
    """Run one VNET deploy step with server-derived defaults (aggregated deploy)."""
    import azure_tools
    from naming import sanitize
    vi = req.vnet_info
    details = req.get_details()
    addr = req.allocated_subnet or ""
    if key == "vnet":
        return azure_tools.create_spoke_vnet(
            vi.subscription_id, vi.resource_group, vi.vnet_name, vi.region, addr,
            subnet_name=vi.subnet_name or "default", subnet_size=vi.subnet_size,
            subnets=details.get("subnets") or None)
    if key == "peer":
        pn = details.get("peering_names") or {}
        s2h = sanitize(pn.get("spoke_to_hub") or render_name("TPL_PEERING_SPOKE_TO_HUB", vnet=vi.vnet_name))
        h2s = sanitize(pn.get("hub_to_spoke") or render_name("TPL_PEERING_HUB_TO_SPOKE", vnet=vi.vnet_name))
        return azure_tools.peer_hub_vnet(
            spoke_subscription_id=vi.subscription_id, spoke_resource_group=vi.resource_group,
            spoke_vnet_name=vi.vnet_name, spoke_address_space=addr,
            spoke_to_hub_name=s2h, hub_to_spoke_name=h2s)
    if key == "internet":
        rule_name = render_name("TPL_FW_RULE_NAME", vnet=vi.vnet_name, request_id=req.id,
                                region=vi.region, cidr_mask=addr.split("/")[-1], purpose=req.purpose)
        return azure_tools.allow_internet_rule(addr, rule_name)
    if key in ("gateway_route", "zpa_route"):
        table = (cfg.UDR_GATEWAY_NAME or cfg.UDR_NAME_1) if key == "gateway_route" \
                else (cfg.UDR_ZPA_NAME or cfg.UDR_NAME_2)
        if not table:
            return {"success": False, "message": f"No routing table configured for {key} "
                                                 f"(set UDR_GATEWAY_NAME / UDR_ZPA_NAME)."}
        route_name = render_name("TPL_ROUTE_NAME", vnet=vi.vnet_name, request_id=req.id,
                                 region=vi.region, cidr_mask=addr.split("/")[-1], purpose=req.purpose)
        return azure_tools.add_route_to_table(
            route_table_name=table, resource_group=cfg.UDR_RESOURCE_GROUP, route_name=route_name,
            address_prefix=addr, next_hop_type="VirtualAppliance",
            next_hop_ip=cfg.HUB_FIREWALL_PRIVATE_IP, subscription_id=cfg.HUB_SUBSCRIPTION_ID)
    return {"success": False, "message": f"Cannot auto-deploy '{key}'."}


def _run_reverts(req, changes_list, tag):
    """Undo a list of deployed changes in the given (Azure-dependency-safe) order.
    Records each revert in the change ledger + audit trail. Returns (steps, all_ok)."""
    steps, all_ok = [], True
    for ch in changes_list:
        res = _revert_change(req, ch["key"])
        ok = bool(res.get("success"))
        all_ok = all_ok and ok
        if ok and res.get("change") and not res.get("dry_run"):
            c = res["change"]
            changes.record(action=f"{tag}:{ch['key']}", actor=current_actor(),
                           request_id=req.id, target=c.get("target", ""),
                           summary=str(res.get("message", ""))[:300],
                           before=c.get("before"), after=c.get("after"),
                           revert_op=c.get("revert_op"), revert_params=c.get("revert_params"))
        steps.append({"key": ch["key"], "label": ch["label"], "success": ok,
                      "dry_run": bool(res.get("dry_run")), "message": str(res.get("message", ""))})
        audit.record("azure_revert", actor=current_actor(), actor_role="admin", request_id=req.id,
                     summary=f"Revert '{ch['key']}' — "
                             f"{'dry-run' if res.get('dry_run') else ('ok' if ok else 'FAILED')}: "
                             f"{str(res.get('message', ''))[:200]}",
                     data={"action": ch["key"], "success": ok,
                           "dry_run": bool(res.get("dry_run")),
                           "message": str(res.get("message", ""))[:400]})
    return steps, all_ok


@app.route("/api/admin/requests/<int:req_id>/deployed-changes")
@require_admin
def request_deployed_changes(req_id):
    req = SpokeRequest.query.get_or_404(req_id)
    return jsonify({"changes": _deployed_changes(req), "dry_run_mode": cfg.AZURE_DRY_RUN})


@app.route("/api/admin/requests/<int:req_id>/revert-deployment", methods=["POST"])
@require_admin
def request_revert_deployment(req_id):
    """
    Aggregated revert for a VNET request: tear down EVERYTHING deployed for it —
    firewall rule, hub routes, spoke UDR/route table, peering, then the VNET —
    in the order Azure requires (_REVERT_ORDER), optionally releasing the CIDR.
    The request stays open and resets to its pre-deployment status so it can be
    redeployed; use Cancel/Reject instead to also close it.
    """
    req = SpokeRequest.query.get_or_404(req_id)
    t = req.request_type or RequestType.VNET_NEW
    if t not in (RequestType.VNET_NEW, RequestType.HUB_INTEGRATION):
        return jsonify({"error": "Aggregated deployment revert is only available for VNET requests."}), 400
    if req.status in RequestType.TERMINALS:
        return jsonify({"error": f"Request is already {req.status_label()}."}), 400

    data = request.get_json(force=True) or {}
    release_cidr = bool(data.get("release_cidr", True))
    comment = str(data.get("comment", "")).strip()[:500]
    if not comment:
        return jsonify({"error": "A reason for the revert is required."}), 400

    changes_list = _deployed_changes(req)
    if not release_cidr:
        changes_list = [ch for ch in changes_list if ch["key"] != "cidr"]
    if not changes_list:
        return jsonify({"error": "Nothing deployed to revert for this request."}), 400

    steps, all_ok = _run_reverts(req, changes_list, tag="revert_deploy")
    reverted = sum(1 for s in steps if s["success"])

    # Reset to the pre-deployment state (request stays open).
    cidr_gone = any(s["key"] == "cidr" and s["success"] for s in steps)
    if t == RequestType.HUB_INTEGRATION:
        new_status = RequestStatus.SUBMITTED
    else:                                    # VNET_NEW
        new_status = RequestStatus.CIDR_REQUESTED if cidr_gone else RequestStatus.CIDR_ASSIGNED
    old_status = req.status
    req.status = new_status
    req.updated_at = datetime.utcnow()
    stamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
    note = (f"[{stamp} UTC] Full deployment revert by {current_actor()}: "
            f"{reverted}/{len(steps)} change(s) undone.")
    if comment:
        note += f" — {comment}"
    req.notes = f"{req.notes}\n{note}" if req.notes else note
    db.session.commit()
    audit.record("status_changed", actor=current_actor(), actor_role="admin", request_id=req.id,
                 summary=f"Full deployment revert: {RequestStatus.label(old_status)} → "
                         f"{RequestStatus.label(new_status)} (reverted {reverted}/{len(steps)} change(s))"
                         + (f' — "{comment[:150]}"' if comment else ""),
                 data={"old": old_status, "new": new_status, "reverted": reverted,
                       "revert_steps": len(steps), "aggregated": True, "comment": comment or None})
    return jsonify({"success": all_ok, "status": new_status, "steps": steps,
                    "message": f"Deployment reverted — {reverted}/{len(steps)} change(s) undone. "
                               f"Request reset to {RequestStatus.label(new_status)}."})


@app.route("/api/admin/requests/<int:req_id>/pending-deploy")
@require_admin
def request_pending_deploy(req_id):
    """Preview: the ordered list of deploy steps the aggregated deploy will run."""
    req = SpokeRequest.query.get_or_404(req_id)
    return jsonify({"plan": _pending_deploy_actions(req), "dry_run_mode": cfg.AZURE_DRY_RUN})


@app.route("/api/admin/requests/<int:req_id>/deploy-all", methods=["POST"])
@require_admin
def request_deploy_all(req_id):
    """
    Aggregated deploy for a VNET request: run every required step — create VNET,
    peer with the hub, add the internet-egress rule, add the gateway & ZPA hub
    routes — in the order Azure needs, then advance the status. Each step is
    audited + snapshotted to the change ledger. A specific internet rule
    (network/application access) is left as a manual step because it needs the
    admin to choose a rule collection group & collection.
    """
    req = SpokeRequest.query.get_or_404(req_id)
    t = req.request_type or RequestType.VNET_NEW
    if t not in (RequestType.VNET_NEW, RequestType.HUB_INTEGRATION):
        return jsonify({"error": "Aggregated deploy is only available for VNET requests."}), 400
    if req.status in RequestType.TERMINALS:
        return jsonify({"error": f"Request is already {req.status_label()}."}), 400
    vi = req.vnet_info
    if not vi or not all([vi.subscription_id, vi.resource_group, vi.vnet_name]):
        return jsonify({"error": "VNET details are missing — assign a CIDR / capture VNET info first."}), 400

    plan = _pending_deploy_actions(req)
    if not plan:
        return jsonify({"error": "Nothing left to deploy — all required steps are already done."}), 400

    def _rec_change(res, act):
        ch = res.get("change")
        if res.get("success") and ch and not res.get("dry_run"):
            changes.record(action=act, actor=current_actor(), request_id=req.id,
                           target=ch.get("target", ""), summary=str(res.get("message", ""))[:300],
                           before=ch.get("before"), after=ch.get("after"),
                           revert_op=ch.get("revert_op"), revert_params=ch.get("revert_params"))

    steps, all_ok = [], True
    for item in plan:
        key = item["key"]
        if item.get("manual"):
            all_ok = False
            steps.append({"key": key, "label": item["label"], "success": False, "manual": True,
                          "dry_run": False,
                          "message": "Run this step manually — a specific internet rule needs you to "
                                     "pick a rule collection group & collection."})
            continue
        if key == "spoke_route_table":
            res = _deploy_spoke_route_table(req)   # records its own sub-changes
            ok = bool(res.get("success"))
            all_ok = all_ok and ok
            audit.record("azure_action", actor=current_actor(), actor_role="admin", request_id=req.id,
                         summary=f"Azure action 'spoke_route_table' (deploy-all) — "
                                 f"{'dry-run' if res.get('dry_run') else ('ok' if ok else 'FAILED')}",
                         data={"action": "spoke_route_table", "dry_run": bool(res.get("dry_run")),
                               "success": ok, "message": str(res.get("message", ""))[:400]})
            steps.append({"key": key, "label": item["label"], "success": ok,
                          "conflict": bool(res.get("conflict")), "dry_run": bool(res.get("dry_run")),
                          "message": str(res.get("message", ""))})
            continue
        res = _deploy_one(req, key)
        ok = bool(res.get("success"))
        all_ok = all_ok and ok
        # Persist the side-effects the individual handlers record.
        if key == "vnet" and ok and req.status == RequestStatus.CIDR_ASSIGNED:
            req.status = RequestStatus.VNET_CREATED
            req.updated_at = datetime.utcnow()
            db.session.commit()
            try:
                notifications.notify_vnet_created(req)
            except Exception:
                pass
        if key == "peer" and ok:
            d = req.get_details()
            d["peering_names"] = {
                "spoke_to_hub": res.get("spoke_to_hub_name")
                                or render_name("TPL_PEERING_SPOKE_TO_HUB", vnet=vi.vnet_name),
                "hub_to_spoke": res.get("hub_to_spoke_name")
                                or render_name("TPL_PEERING_HUB_TO_SPOKE", vnet=vi.vnet_name)}
            req.set_details(d)
            db.session.commit()
        _rec_change(res, key)
        audit.record("azure_action", actor=current_actor(), actor_role="admin", request_id=req.id,
                     summary=f"Azure action '{key}' (deploy-all) — "
                             f"{'dry-run' if res.get('dry_run') else ('ok' if ok else 'FAILED')}",
                     data={"action": key, "dry_run": bool(res.get("dry_run")),
                           "success": ok, "message": str(res.get("message", ""))[:400]})
        steps.append({"key": key, "label": item["label"], "success": ok,
                      "conflict": bool(res.get("conflict")), "dry_run": bool(res.get("dry_run")),
                      "message": str(res.get("message", ""))})

    _auto_advance(req)
    done_n = sum(1 for s in steps if s["success"])
    db.session.refresh(req)
    audit.record("status_changed" if done_n else "azure_action", actor=current_actor(),
                 actor_role="admin", request_id=req.id,
                 summary=f"Aggregated deploy: {done_n}/{len(steps)} step(s) succeeded "
                         f"(status now {req.status_label()}).",
                 data={"aggregated_deploy": True, "succeeded": done_n, "steps": len(steps)})
    return jsonify({"success": all_ok, "status": req.status, "steps": steps,
                    "message": f"Deploy complete — {done_n}/{len(steps)} step(s) succeeded. "
                               f"Status now {req.status_label()}."
                               + ("" if all_ok else " Some steps need attention (see below).")})


@app.route("/api/admin/requests/<int:req_id>/terminate", methods=["POST"])
@require_admin
def request_terminate(req_id):
    """
    Cancel or reject a request, reverting every deployed Azure change first
    (unless revert=false). Each revert step is audited individually.
    """
    req = SpokeRequest.query.get_or_404(req_id)
    data = request.get_json(force=True) or {}
    target = str(data.get("status", "")).strip()
    do_revert = bool(data.get("revert", True))
    comment = str(data.get("comment", "")).strip()[:500]
    if target not in RequestType.TERMINALS:
        return jsonify({"error": "Target status must be CANCELLED or REJECTED."}), 400
    if req.status in RequestType.TERMINALS:
        return jsonify({"error": f"Request is already {req.status_label()}."}), 400

    steps, all_ok = [], True
    if do_revert:
        # Reverting deployed changes always requires a reason.
        if _deployed_changes(req) and not comment:
            return jsonify({"error": "A reason/comment is required when reverting deployed changes."}), 400
        steps, all_ok = _run_reverts(req, _deployed_changes(req), tag="cancel_revert")

    old_status = req.status
    req.status = target
    req.updated_at = datetime.utcnow()
    reverted = sum(1 for s in steps if s["success"])
    stamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
    notes = []
    if comment:
        notes.append(f"[{stamp} UTC] {RequestStatus.label(target)} by {current_actor()}: {comment}")
    if steps:
        notes.append(f"[{stamp} UTC] {RequestStatus.label(target)} with revert: "
                     f"{reverted}/{len(steps)} change(s) undone.")
    if notes:
        joined = "\n".join(notes)
        req.notes = f"{req.notes}\n{joined}" if req.notes else joined
    db.session.commit()
    audit.record("status_changed", actor=current_actor(), actor_role="admin", request_id=req.id,
                 summary=f"Status: {RequestStatus.label(old_status)} → {RequestStatus.label(target)}"
                         + (f" (reverted {reverted}/{len(steps)} deployed change(s))" if steps else "")
                         + (f' — "{comment[:150]}"' if comment else ""),
                 data={"old": old_status, "new": target, "reverted": reverted,
                       "revert_steps": len(steps), "comment": comment or None})
    try:
        notifications.notify_status_changed(req)
    except Exception:
        pass
    return jsonify({"success": all_ok, "status": target, "steps": steps,
                    "message": f"Request {RequestStatus.label(target).lower()}."
                               + (f" {reverted}/{len(steps)} change(s) reverted." if steps else "")})


# ═══════════════════════════════════════════════════════════════════════════
# Admin Agent (protected)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/agent")
@require_admin
def agent_page():
    req_id = request.args.get("req")
    req_obj = SpokeRequest.query.get(int(req_id)) if req_id and req_id.isdigit() else None
    return render_template("agent.html", request_obj=req_obj)


@app.route("/api/agent/chats")
@require_admin
def agent_chats_list():
    import chats
    return jsonify({"chats": chats.list_chats("admin", _chat_owner("admin"))})


@app.route("/api/agent/chats/<int:cid>")
@require_admin
def agent_chat_get(cid):
    import chats
    if not chats.owns(cid, "admin", _chat_owner("admin")):
        return jsonify({"error": "Chat not found."}), 404
    ch = chats.get_chat(cid)
    return jsonify({"id": ch["id"], "title": ch["title"],
                    "messages": [{"role": m.get("role"), "content": m.get("content", "")}
                                 for m in ch["messages"]]})


@app.route("/api/agent/chats/<int:cid>", methods=["DELETE"])
@require_admin
def agent_chat_delete(cid):
    import chats
    chats.delete_chat(cid, _chat_owner("admin"))
    return jsonify({"success": True})


@app.route("/api/agent/chat", methods=["POST"])
@require_admin
def agent_chat():
    import chats
    try:
        data = request.get_json(force=True)
        user_msg = (data.get("message") or "").strip()
        if not user_msg:
            return jsonify({"error": "Empty message"}), 400

        owner = _chat_owner("admin")
        chat_id = data.get("chat_id")
        if not (chat_id and str(chat_id).isdigit() and chats.owns(int(chat_id), "admin", owner)):
            chat_id = chats.create_chat("admin", owner)
        chat_id = int(chat_id)
        ch = chats.get_chat(chat_id)
        history = [{"role": m.get("role"), "content": m.get("content", "")}
                   for m in (ch["messages"] if ch else [])]
        history.append({"role": "user", "content": user_msg})

        reply, tool_calls = "Agent error.", []
        try:
            import agent_admin as ag
            result = ag.chat(history)
            reply = result.get("reply", "")
            for tc in result.get("tool_calls", []):
                tool_calls.append({"tool": str(tc.get("tool", "")), "status": str(tc.get("status", ""))})
        except Exception as exc:
            log.exception("Admin agent error")
            reply = f"Agent error: {exc}"

        chats.append_messages(chat_id,
                              [{"role": "user", "content": user_msg},
                               {"role": "assistant", "content": reply}],
                              title_hint=user_msg)
        return jsonify({"reply": reply, "tool_calls": tool_calls, "chat_id": chat_id})
    except Exception as exc:
        log.exception("Admin agent chat route error")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    # Debug (Werkzeug reloader + interactive debugger) is opt-in via FLASK_DEBUG.
    # Default off — the debugger is an RCE vector if the port is ever exposed.
    app.run(host="0.0.0.0", port=8080, debug=cfg.DEBUG)
