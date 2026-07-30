"""
Line-manager approval flow.

Optional gate that holds selected request types until the requester's line
manager approves. Approver routing is relationship-based (the requester's
manager), never a single global approver — the manager identity is sourced from
an OIDC token claim that Keycloak maps from the Entra ID 'manager' attribute.

This module owns:
  * policy resolution (per request type: mode + gate timing),
  * the enable/dependency preflight (auto-disable when prerequisites are unmet),
  * manager-claim observation tracking (proves the Entra→Keycloak mapping works),
  * the Approval lifecycle helpers (open a gate, decide, check for a valid one).

Everything here is inert unless the master switch is on AND the preflight passes,
so an un-configured portal behaves exactly as before.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import settings_store
from config import cfg
from models import db, Approval, SpokeRequest, RequestStatus, RequestType

log = logging.getLogger(__name__)

# ── Vocabulary ───────────────────────────────────────────────────────────────
MODE_NONE       = "none"        # no approval for this type
MODE_DISCRETION = "discretion"  # admin may send this specific request for approval
MODE_REQUIRED   = "required"    # every request of this type must be approved

TIMING_SUBMISSION = "submission"  # held right after it's raised
TIMING_TRIGGER    = "trigger"     # the actual Azure deploy is blocked until approved
TIMING_BOTH       = "both"

GATE_SUBMISSION = "submission"
GATE_TRIGGER    = "trigger"

MODES   = (MODE_NONE, MODE_DISCRETION, MODE_REQUIRED)
TIMINGS = (TIMING_SUBMISSION, TIMING_TRIGGER, TIMING_BOTH)

# Runtime observation of the manager claim (not a user-facing setting).
_MGR_STATE_KEY = "_APPROVAL_MGR_STATE"


# ── Policy ───────────────────────────────────────────────────────────────────

def _raw_policy() -> dict:
    try:
        return json.loads(cfg.APPROVAL_POLICY) if cfg.APPROVAL_POLICY else {}
    except Exception:
        log.warning("APPROVAL_POLICY is not valid JSON — treating as empty.")
        return {}


def policy_for(request_type: str) -> dict:
    """Effective {mode, timing} for a request type. Everything defaults to
    'not required'; a configured entry may set mode and (optionally) timing."""
    entry = _raw_policy().get(request_type) or {}
    mode = entry.get("mode") if entry.get("mode") in MODES else MODE_NONE
    timing = entry.get("timing") if entry.get("timing") in TIMINGS else (cfg.APPROVAL_DEFAULT_TIMING or TIMING_SUBMISSION)
    return {"mode": mode, "timing": timing}


def policy_matrix() -> list:
    """View model for the settings matrix: one row per request type."""
    rows = []
    for t in RequestType.ALL:
        p = policy_for(t)
        rows.append({"type": t, "label": RequestType.label(t),
                     "mode": p["mode"], "timing": p["timing"]})
    return rows


def _timing_covers(timing: str, gate: str) -> bool:
    if timing == TIMING_BOTH:
        return True
    return timing == gate


# ── Manager-claim observation (dependency signal) ────────────────────────────

def _mgr_state() -> dict:
    raw = settings_store.get_override(_MGR_STATE_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def record_login_observation(manager_present: bool, manager_value: str = ""):
    """Called at each SSO login. `manager_present` = the configured claim key was
    present in the token (even if empty) — that proves the Keycloak mapper fired."""
    st = _mgr_state()
    st["last_checked_at"] = datetime.utcnow().isoformat(timespec="seconds")
    if manager_present:
        st["ever_seen"] = True
        st["last_seen_at"] = st["last_checked_at"]
        if manager_value:
            st["last_value_seen_at"] = st["last_checked_at"]
    try:
        settings_store.set_override(_MGR_STATE_KEY, json.dumps(st))
    except Exception:
        log.exception("Could not persist approval manager-claim observation")


def manager_seen() -> dict:
    st = _mgr_state()
    return {"ever_seen": bool(st.get("ever_seen")),
            "last_seen_at": st.get("last_seen_at"),
            "last_checked_at": st.get("last_checked_at")}


# ── Preflight / dependency check ─────────────────────────────────────────────

def preflight() -> dict:
    """Static + observed dependency checks. Returns {ok, checks:[...]}. `ok` is the
    gate for actually running approvals; a failed check auto-disables the feature."""
    checks = []

    sso = (cfg.AUTH_PROVIDER == "keycloak")
    checks.append({"name": "SSO provider is Keycloak", "ok": sso,
                   "detail": "" if sso else "Set Authentication → Auth provider to 'keycloak'. "
                             "Approvals need real identities and a manager relationship."})

    kc = all([cfg.KEYCLOAK_SERVER_URL, cfg.KEYCLOAK_REALM, cfg.KEYCLOAK_CLIENT_ID])
    checks.append({"name": "Keycloak connection configured", "ok": bool(kc),
                   "detail": "" if kc else "Fill Keycloak server URL, realm and client ID under Authentication."})

    claim = bool((cfg.APPROVAL_MANAGER_CLAIM or "").strip())
    checks.append({"name": "Manager claim name set", "ok": claim,
                   "detail": "" if claim else "Set the 'Manager token claim' name (e.g. 'manager')."})

    seen = manager_seen()
    checks.append({"name": "Manager claim detected in a login token", "ok": seen["ever_seen"],
                   "detail": "" if seen["ever_seen"] else
                   "Not seen yet. Configure the Entra→Keycloak manager mapping (see prerequisites), "
                   "then sign in once so the platform can confirm the claim is present.",
                   "meta": seen})

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks}


def enabled() -> bool:
    """Effective switch: master flag ON and every dependency satisfied."""
    if not cfg.APPROVALS_ENABLED:
        return False
    return preflight()["ok"]


# ── Manager resolution ───────────────────────────────────────────────────────

def resolve_approver(manager_email: str = "", manager_name: str = "") -> dict:
    """Pick the approver for a new checkpoint. The requester's line manager if we
    have one, else the configured fallback, else 'any super-admin' (email blank)."""
    manager_email = (manager_email or "").strip()
    if manager_email:
        return {"email": manager_email, "name": (manager_name or manager_email).strip(), "via": "manager"}
    fb = (cfg.APPROVAL_FALLBACK_EMAIL or "").strip()
    if fb:
        return {"email": fb, "name": fb, "via": "fallback"}
    return {"email": "", "name": "Super-admin", "via": "fallback"}


def can_decide(appr: Approval, actor_email: str, actor_name: str, is_superadmin: bool) -> bool:
    """Is this actor allowed to approve/reject this checkpoint?"""
    actor_email = (actor_email or "").strip().lower()
    # Prevent self-approval (the requester can never approve their own request).
    if cfg.APPROVAL_PREVENT_SELF and actor_email and actor_email == (appr.requested_by or "").strip().lower():
        return False
    if appr.assigned_via == "manager":
        assigned = (appr.assigned_to_email or "").strip().lower()
        return bool(actor_email and actor_email == assigned) or is_superadmin
    # fallback checkpoint
    fb = (appr.assigned_to_email or "").strip().lower()
    if fb:
        return bool(actor_email and actor_email == fb) or is_superadmin
    return is_superadmin


# ── Lifecycle ────────────────────────────────────────────────────────────────

def _sync_request_state(req: SpokeRequest):
    """Recompute the cached approval_state on the request from its checkpoints."""
    apprs = Approval.query.filter_by(request_id=req.id).all()
    if not apprs:
        req.approval_state = "not_required"
    elif any(a.status == "rejected" for a in apprs):
        req.approval_state = "rejected"
    elif any(a.status == "pending" for a in apprs):
        req.approval_state = "pending"
    else:
        req.approval_state = "approved"


def open_submission_gate(req: SpokeRequest, requester_email: str, requester_name: str,
                         manager_email: str = "", manager_name: str = "") -> Approval | None:
    """If this request's type requires approval at submission, create the checkpoint
    and hold the request. Returns the Approval, or None if no gate applies."""
    if not enabled():
        return None
    pol = policy_for(req.request_type or RequestType.VNET_NEW)
    if pol["mode"] != MODE_REQUIRED or not _timing_covers(pol["timing"], GATE_SUBMISSION):
        return None
    who = resolve_approver(manager_email, manager_name)
    appr = Approval(
        request_id=req.id, gate=GATE_SUBMISSION, action_key=None, status="pending",
        requested_by=(requester_email or requester_name or ""),
        assigned_to_email=who["email"] or None, assigned_to_name=who["name"], assigned_via=who["via"],
    )
    db.session.add(appr)
    req.status = RequestStatus.PENDING_APPROVAL
    req.updated_at = datetime.utcnow()
    _sync_request_state(req)
    db.session.commit()
    return appr


def request_discretion_approval(req: SpokeRequest, requested_by: str,
                                manager_email: str = "", manager_name: str = "") -> Approval:
    """Admin-initiated: send a specific request for approval (discretion mode)."""
    who = resolve_approver(manager_email, manager_name)
    appr = Approval(
        request_id=req.id, gate=GATE_SUBMISSION, action_key=None, status="pending",
        requested_by=requested_by or "",
        assigned_to_email=who["email"] or None, assigned_to_name=who["name"], assigned_via=who["via"],
    )
    db.session.add(appr)
    req.status = RequestStatus.PENDING_APPROVAL
    req.updated_at = datetime.utcnow()
    _sync_request_state(req)
    db.session.commit()
    return appr


def needs_trigger_approval(req: SpokeRequest, action_key: str = None) -> bool:
    """Does an Azure deploy for this request require a granted trigger approval?"""
    if not enabled():
        return False
    pol = policy_for(req.request_type or RequestType.VNET_NEW)
    if pol["mode"] != MODE_REQUIRED or not _timing_covers(pol["timing"], GATE_TRIGGER):
        return False
    return not has_valid_trigger_approval(req, action_key)


def has_valid_trigger_approval(req: SpokeRequest, action_key: str = None) -> bool:
    """A granted, not-yet-consumed trigger approval covering this request."""
    q = Approval.query.filter_by(request_id=req.id, gate=GATE_TRIGGER, status="approved")
    return db.session.query(q.exists()).scalar()


def open_trigger_gate(req: SpokeRequest, action_key: str, requested_by: str,
                      manager_email: str = "", manager_name: str = "") -> Approval:
    """Raise a pending trigger checkpoint (called when a blocked deploy is attempted)."""
    existing = Approval.query.filter_by(request_id=req.id, gate=GATE_TRIGGER,
                                        status="pending").first()
    if existing:
        return existing
    who = resolve_approver(manager_email, manager_name)
    appr = Approval(
        request_id=req.id, gate=GATE_TRIGGER, action_key=action_key, status="pending",
        requested_by=requested_by or "",
        assigned_to_email=who["email"] or None, assigned_to_name=who["name"], assigned_via=who["via"],
    )
    db.session.add(appr)
    _sync_request_state(req)
    db.session.commit()
    return appr


def decide(appr: Approval, decision: str, decider_email: str, decider_name: str,
           reason: str = "") -> None:
    """Record an approve/reject decision and reconcile the request status."""
    appr.status = "approved" if decision == "approve" else "rejected"
    appr.decided_by = decider_name or decider_email
    appr.decided_at = datetime.utcnow()
    appr.decision_reason = (reason or "").strip()[:1000] or None

    req = SpokeRequest.query.get(appr.request_id)
    if req:
        if appr.status == "rejected":
            req.status = RequestStatus.REJECTED
        elif appr.gate == GATE_SUBMISSION and req.status == RequestStatus.PENDING_APPROVAL:
            # Release the request back into its normal starting status.
            req.status = RequestType.initial_status(req.request_type or RequestType.VNET_NEW)
        req.updated_at = datetime.utcnow()
        _sync_request_state(req)
    db.session.commit()


def pending_for(actor_email: str, is_superadmin: bool) -> list:
    """Approvals awaiting this actor's decision (their reports' requests, plus
    fallback items for super-admins)."""
    actor_email = (actor_email or "").strip().lower()
    q = Approval.query.filter_by(status="pending").order_by(Approval.requested_at.desc())
    out = []
    for a in q.all():
        if can_decide(a, actor_email, "", is_superadmin):
            out.append(a)
    return out


def for_request(req_id: int) -> list:
    return Approval.query.filter_by(request_id=req_id).order_by(Approval.created_at.asc()).all()
