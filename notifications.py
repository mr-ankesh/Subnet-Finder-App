"""
Teams notification helpers — Power Automate Workflows webhook (Adaptive Card format).
"""
import json
import logging
import re
import smtplib
from email.message import EmailMessage

import requests as http_requests
from config import cfg

log = logging.getLogger(__name__)

ICON = {"info": "🔵", "success": "✅", "warning": "🟡", "danger": "🔴"}


def _adaptive_card(title, subtitle, body_text, facts, color="info", action_url=None, action_label="View") -> dict:
    card_body = [
        {"type": "TextBlock", "text": f"{ICON.get(color,'🔵')} {title}",
         "weight": "Bolder", "size": "Medium", "wrap": True, "color": "Accent"},
        {"type": "TextBlock", "text": subtitle, "size": "Small", "color": "Subtle",
         "wrap": True, "spacing": "None"},
    ]
    if body_text:
        card_body.append({"type": "TextBlock", "text": body_text, "wrap": True, "spacing": "Medium"})
    if facts:
        card_body.append({"type": "FactSet", "facts": facts, "spacing": "Medium"})

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.5", "body": card_body,
    }
    if action_url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": action_label, "url": action_url}]

    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}
    ]}


def _post(payload: dict) -> bool:
    if not cfg.TEAMS_WEBHOOK_URL:
        log.warning("TEAMS_WEBHOOK_URL not set — notification skipped.")
        return False
    try:
        resp = http_requests.post(
            cfg.TEAMS_WEBHOOK_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code not in (200, 202):
            log.error("Teams webhook %s: %s", resp.status_code, resp.text[:300])
            return False
        return True
    except Exception as exc:
        log.error("Teams notification failed: %s", exc)
        return False


def _url(path: str) -> str | None:
    base = cfg.SUBNET_FINDER_BASE_URL.strip()
    if not base:
        return None
    return f"{base.rstrip('/')}{path}"


# ── Email (SMTP) ──────────────────────────────────────────────────────────
def _send_email(to, subject: str, body_text: str) -> bool:
    """Send to one address or a list. No-op (returns False) if SMTP unconfigured."""
    recipients = [to] if isinstance(to, str) else list(to or [])
    recipients = list(dict.fromkeys(r.strip() for r in recipients if r and r.strip()))
    if not cfg.SMTP_HOST:
        log.info("SMTP_HOST not set — email skipped (%s).", ", ".join(recipients) or "no recipients")
        return False
    if not recipients:
        return False
    try:
        msg = EmailMessage()
        msg["From"] = cfg.SMTP_FROM or cfg.SMTP_USER or "noreply@localhost"
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(body_text)
        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=15) as s:
            if cfg.SMTP_USE_TLS:
                s.starttls()
            if cfg.SMTP_USER:
                s.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            s.send_message(msg)
        log.info("Email sent to %s: %s", msg["To"], subject)
        return True
    except Exception as exc:
        log.error("Email to %s failed: %s", ", ".join(recipients), exc)
        return False


def _notify_emails() -> list:
    """Corporate recipient list from NOTIFY_EMAILS (comma/semicolon separated)."""
    raw = (cfg.NOTIFY_EMAILS or "").replace(";", ",")
    return [e.strip() for e in raw.split(",") if e.strip()]


# ── Intelligent drafting — LLM writes the email for the specific case ─────
def _looks_unusable(s: str) -> bool:
    """Reject leaked reasoning / non-English output: significant CJK content, or
    obvious chain-of-thought markers. Guards against models that 'think' in plain
    text (no <think> tags) or in another language before answering."""
    if not s or not s.strip():
        return True
    if sum(1 for ch in s if "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ") > 3:
        return True   # Chinese/Japanese characters → reasoning leak
    return False


def _parse_draft(text: str) -> tuple | None:
    """Parse a well-formed LLM draft ('Subject: …\\n\\n<body>') into (subject, body).

    Returns None when the output doesn't strictly match — no leading Subject line,
    empty parts, or leaked reasoning — so the caller falls back to the template
    rather than ever emitting a malformed / chain-of-thought email.
    """
    text = (text or "").strip()
    m = re.match(r"(?is)^subject:\s*(.+?)\r?\n+(.*)$", text)   # anchored: must LEAD with Subject:
    if not m:
        return None
    subject, body = m.group(1).strip(), m.group(2).strip()
    if not subject or not body or len(body) > 1500:
        return None
    if _looks_unusable(subject) or _looks_unusable(body):
        return None
    return subject, body


def _draft_email(event: str, req, facts: list, fallback_subject: str, fallback_body: str) -> tuple:
    """Ask the LLM to write a notification email tailored to this case. Best-effort:
    returns the provided template fallbacks if drafting is disabled or fails."""
    if not cfg.NOTIFY_AI_DRAFT:
        return fallback_subject, fallback_body
    try:
        import netdiag  # reuses the admin agent's client + <think>-stripping
        if not netdiag._llm_available():
            return fallback_subject, fallback_body
        type_label = req.type_label() if hasattr(req, "type_label") else "Network request"
        status = req.status_label() if hasattr(req, "status_label") else ""
        factlines = "\n".join(f"- {f['title']}: {f['value']}" for f in (facts or [])
                              if f.get("value") not in (None, "", False))
        link = _url(f"/requests/{req.id}") or ""
        system = (
            "You are AlMadar 360, the notification assistant for Presight R&D's Azure "
            "hub-and-spoke network operations portal. Write a SHORT, professional internal "
            "notification email about a change to a network request, for the network operations "
            "team (and the requester, who is copied). "
            "Respond in ENGLISH ONLY. Do NOT think out loud, explain your reasoning, or emit any "
            "chain-of-thought / analysis / <think> tags in ANY language — output ONLY the finished "
            "email. Your FIRST characters must be the literal text 'Subject:'. "
            "Format EXACTLY: a first line 'Subject: <concise subject>', then a blank line, then the "
            "body — plain text, 2-5 short sentences, no markdown, no greeting to a named person, no "
            "signature block. Clearly state what happened, the most relevant details, and any action "
            "the team must take. Use ONLY the facts provided — do not invent details.")
        user = (f"Event: {event}\n"
                f"Request ID: #{req.id}\n"
                f"Type: {type_label}\n"
                f"Current status: {status or '(n/a)'}\n"
                f"Requester: {getattr(req, 'requester_name', '') or '(unknown)'}\n"
                f"Purpose: {getattr(req, 'purpose', '') or '(none)'}\n"
                f"Key details:\n{factlines or '(none)'}\n")
        parsed = _parse_draft(netdiag._llm_complete(system, user))
        if not parsed:
            log.info("email draft malformed for #%s — using template", getattr(req, "id", "?"))
            return fallback_subject, fallback_body
        subject, body = parsed
        if link and link not in body:
            body += f"\n\nView the request: {link}"
        return subject, body
    except Exception as exc:
        log.error("email draft failed, using template: %s", exc)
        return fallback_subject, fallback_body


def _email_case(req, event: str, fallback_subject: str, fallback_body: str,
                facts: list = None, to_requester: bool = True) -> bool:
    """Send a case notification email to the corporate recipients (+ the requester
    when to_requester), with the body drafted intelligently for this event."""
    recipients = _notify_emails()
    if to_requester:
        r = getattr(req, "requester_email", None)
        if r:
            recipients = recipients + [r]
    if not recipients or not cfg.SMTP_HOST:
        return False
    link = _url(f"/requests/{req.id}") or ""
    footer = f"\n\nView the request: {link}" if link and link not in fallback_body else ""
    subject, body = _draft_email(event, req, facts, fallback_subject, fallback_body + footer)
    return _send_email(recipients, subject, body)


# Back-compat shim: existing callers that only reach the requester.
def _email_requester(req, subject: str, body_text: str) -> bool:
    return _email_case(req, event=subject, fallback_subject=subject,
                       fallback_body=body_text, facts=None, to_requester=True)


# ── Generic: any request type submitted (non-VNET types) ─────────────────
def notify_request_submitted(req) -> bool:
    """Teams card + requester ack for non-VNET request types (firewall, ZPA, DNS…)."""
    type_label = req.type_label() if hasattr(req, "type_label") else "Request"
    facts = [
        {"title": "Request ID", "value": f"#{req.id}"},
        {"title": "Type",       "value": type_label},
        {"title": "Requester",  "value": req.requester_name},
        {"title": "Summary",    "value": req.purpose},
    ]
    details = req.get_details() if hasattr(req, "get_details") else {}
    for k, v in list(details.items())[:6]:      # keep the card compact
        if v not in (None, "", False):
            facts.append({"title": k.replace("_", " ").title(), "value": str(v)})
    _email_case(req, f"A new {type_label} request was submitted and awaits admin review",
                f"[AlMadar 360] {type_label} request received — #{req.id}",
                f"Your {type_label} request #{req.id} has been submitted and is awaiting admin "
                f"review.\n\nSummary: {req.purpose}", facts=facts)
    return _post(_adaptive_card(
        title=f"New {type_label} Request #{req.id}",
        subtitle="Presight R&D · AlMadar 360",
        body_text=f"**{req.requester_name}** has submitted a **{type_label}** request.",
        facts=facts, color="info",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Generic: status changed (non-VNET types) ──────────────────────────────
def notify_status_changed(req) -> bool:
    type_label = req.type_label() if hasattr(req, "type_label") else "Request"
    status_label = req.status_label()
    facts = [
        {"title": "Request ID", "value": f"#{req.id}"},
        {"title": "Type",       "value": type_label},
        {"title": "New Status", "value": status_label},
        {"title": "Requester",  "value": req.requester_name},
    ]
    _email_case(req, f"Request status changed to '{status_label}'",
                f"[AlMadar 360] Request #{req.id} → {status_label}",
                f"{type_label} request #{req.id} is now: {status_label}.", facts=facts)
    return _post(_adaptive_card(
        title=f"Request #{req.id} ({type_label}) → {status_label}",
        subtitle="Presight R&D · AlMadar 360",
        body_text=f"Status updated to **{status_label}**.",
        facts=facts,
        color="success" if req.status in ("COMPLETED", "HUB_INTEGRATED") else "info",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Step 1: CIDR Requested ────────────────────────────────────────────────
def notify_cidr_requested(req) -> bool:
    facts = [
        {"title": "Request ID",      "value": f"#{req.id}"},
        {"title": "Requester",       "value": req.requester_name},
        {"title": "CIDR Needed",     "value": f"/{req.cidr_needed}"},
        {"title": "IP Pool",         "value": req.ip_range},
        {"title": "Purpose",         "value": req.purpose},
        {"title": "Hub Integration", "value": "Yes" if req.hub_integration else "No"},
    ]
    _email_case(req, "A new spoke CIDR request was submitted; admin needs to assign a subnet",
                f"[AlMadar 360] New CIDR request — #{req.id}",
                f"A new spoke CIDR request #{req.id} from {req.requester_name} is awaiting subnet "
                f"assignment.\n\nCIDR needed: /{req.cidr_needed}\nPool: {req.ip_range}\n"
                f"Purpose: {req.purpose}", facts=facts)
    return _post(_adaptive_card(
        title=f"New CIDR Request #{req.id}",
        subtitle="Presight R&D · AlMadar 360",
        body_text=f"**{req.requester_name}** has submitted a new spoke CIDR request and is awaiting admin assignment.",
        facts=facts, color="info",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Step 2: CIDR Assigned ─────────────────────────────────────────────────
def notify_cidr_assigned(req, subnet: str) -> bool:
    facts = [
        {"title": "Request ID",   "value": f"#{req.id}"},
        {"title": "Requester",    "value": req.requester_name},
        {"title": "CIDR Assigned","value": subnet},
        {"title": "Pool",         "value": req.ip_range},
    ]
    body = f"Subnet **{subnet}** has been assigned to request #{req.id}. Requester can now deploy the spoke VNET."
    _email_case(req, f"Subnet {subnet} was assigned; the requester can now deploy the spoke VNET",
                f"[AlMadar 360] CIDR {subnet} assigned — Request #{req.id}",
                f"Spoke CIDR request #{req.id} has been assigned the subnet {subnet}. "
                f"The requester can now deploy the spoke VNET.", facts=facts)
    return _post(_adaptive_card(
        title=f"CIDR Assigned — Request #{req.id}",
        subtitle="Presight R&D · AlMadar 360",
        body_text=body, facts=facts, color="success",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Step 3a: VNET Created ─────────────────────────────────────────────────
def notify_vnet_created(req) -> bool:
    facts = [
        {"title": "Request ID",    "value": f"#{req.id}"},
        {"title": "Requester",     "value": req.requester_name},
        {"title": "Subnet",        "value": req.allocated_subnet or "—"},
        {"title": "Hub Required",  "value": "Yes" if req.hub_integration else "No"},
    ]
    _email_case(req, "The requester confirmed their spoke VNET is created",
                f"[AlMadar 360] VNET created — Request #{req.id}",
                f"The spoke VNET for request #{req.id} (subnet {req.allocated_subnet or '—'}) "
                f"has been created.", facts=facts)
    return _post(_adaptive_card(
        title=f"VNET Created — Request #{req.id}",
        subtitle="Presight R&D · AlMadar 360",
        body_text="The requester has confirmed their spoke VNET is created.",
        facts=facts, color="info",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Step 3b: Hub Integration Needed ──────────────────────────────────────
def notify_hub_integration_needed(req) -> bool:
    facts = [
        {"title": "Request ID",   "value": f"#{req.id}"},
        {"title": "Requester",    "value": req.requester_name},
        {"title": "Subnet",       "value": req.allocated_subnet or "—"},
    ]
    vi = req.vnet_info
    if vi:
        facts += [
            {"title": "VNET Name",      "value": vi.vnet_name or "—"},
            {"title": "Resource Group", "value": vi.resource_group or "—"},
            {"title": "Address Space",  "value": vi.address_space or "—"},
            {"title": "VPN/ZPA Access", "value": "Yes" if vi.vpn_zpa_access else "No"},
        ]
    _email_case(req, "The requester provided VNET details and is requesting hub integration; admin action required",
                f"[AlMadar 360] Hub integration requested — Request #{req.id}",
                f"Request #{req.id} from {req.requester_name} has provided its VNET details and is "
                f"requesting hub integration. Admin action is required to proceed.", facts=facts)
    return _post(_adaptive_card(
        title=f"Hub Integration Needed — Request #{req.id}",
        subtitle="Presight R&D · AlMadar 360",
        body_text="The requester has provided VNET details and is requesting hub integration. Admin action required.",
        facts=facts, color="warning",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Step 4a: Hub Integration In Progress ─────────────────────────────────
def notify_hub_in_progress(req) -> bool:
    facts = [
        {"title": "Request ID", "value": f"#{req.id}"},
        {"title": "Requester",  "value": req.requester_name},
        {"title": "Subnet",     "value": req.allocated_subnet or "—"},
    ]
    _email_case(req, "Admin started hub integration for the spoke VNET",
                f"[AlMadar 360] Hub integration started — Request #{req.id}",
                f"Hub integration for the spoke VNET (request #{req.id}) has started.", facts=facts)
    return _post(_adaptive_card(
        title=f"Hub Integration In Progress — Request #{req.id}",
        subtitle="Presight R&D · AlMadar 360",
        body_text="Admin has started hub integration for this spoke VNET.",
        facts=facts, color="info",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Step 4b: Hub Integrated ───────────────────────────────────────────────
def notify_hub_integrated(req, actions_taken: list = None) -> bool:
    facts = [
        {"title": "Request ID", "value": f"#{req.id}"},
        {"title": "Requester",  "value": req.requester_name},
        {"title": "Subnet",     "value": req.allocated_subnet or "—"},
    ]
    action_text = ""
    if actions_taken:
        action_text = "\n\n" + "  \n".join(f"• {a}" for a in actions_taken)
        facts.append({"title": "Actions taken", "value": "; ".join(actions_taken)})
    _email_case(req, "Hub integration completed — the spoke VNET is fully onboarded",
                f"[AlMadar 360] Request #{req.id} complete — hub integrated",
                f"The spoke VNET (request #{req.id}, subnet "
                f"{getattr(req, 'allocated_subnet', None) or '—'}) is fully integrated with the hub. "
                f"Onboarding is complete.", facts=facts)
    return _post(_adaptive_card(
        title=f"Hub Integration Complete — Request #{req.id}",
        subtitle="Presight R&D · AlMadar 360",
        body_text=f"All hub integration tasks have been completed successfully.{action_text}",
        facts=facts, color="success",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Generic / reminder ────────────────────────────────────────────────────
def notify_custom(title: str, message: str, level: str = "info") -> bool:
    # A custom message is already written, so send it verbatim (no LLM redraft)
    # to the corporate recipients in addition to Teams.
    _send_email(_notify_emails(), f"[AlMadar 360] {title}", message)
    return _post(_adaptive_card(
        title=title,
        subtitle="Presight R&D · AlMadar 360",
        body_text=message, facts=[], color=level,
    ))


def notify_reminder(req, message: str) -> bool:
    facts = [
        {"title": "Request ID", "value": f"#{req.id}"},
        {"title": "Requester",  "value": req.requester_name},
        {"title": "Status",     "value": req.status_label()},
        {"title": "Message",    "value": message},
    ]
    _email_case(req, "The requester is following up on a pending request",
                f"[AlMadar 360] Reminder — Request #{req.id}",
                f"{req.requester_name} is following up on request #{req.id} "
                f"(status: {req.status_label()}).\n\nMessage: {message}",
                facts=facts, to_requester=False)
    return _post(_adaptive_card(
        title=f"Reminder — Request #{req.id}",
        subtitle="Presight R&D · AlMadar 360",
        body_text=f"**{req.requester_name}** is following up on their request.",
        facts=facts, color="warning",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Approval flow ─────────────────────────────────────────────────────────
def notify_approval_requested(req, appr) -> bool:
    """Tell the assigned approver (the requester's line manager) that a request
    awaits their decision. Emails the approver directly when we have their address."""
    type_label = req.type_label() if hasattr(req, "type_label") else "Request"
    gate = "before deployment" if getattr(appr, "gate", "") == "trigger" else "before it proceeds"
    facts = [
        {"title": "Request ID", "value": f"#{req.id}"},
        {"title": "Type",       "value": type_label},
        {"title": "Requester",  "value": req.requester_name},
        {"title": "Summary",    "value": req.purpose},
        {"title": "Approver",   "value": getattr(appr, "assigned_to_name", "") or "Super-admin"},
    ]
    subject = f"[AlMadar 360] Approval needed — {type_label} request #{req.id}"
    body = (f"A {type_label} request (#{req.id}) raised by {req.requester_name} requires your "
            f"approval {gate}.\n\nSummary: {req.purpose}\n\n"
            f"Review and approve or reject it: {_url('/approvals') or '(portal)'}")
    # Email the approver directly (if known) plus the corporate list.
    recipients = _notify_emails()
    to = getattr(appr, "assigned_to_email", "") or ""
    if to:
        recipients = recipients + [to]
    if recipients and cfg.SMTP_HOST:
        _send_email(recipients, subject, body)
    return _post(_adaptive_card(
        title=f"Approval needed — Request #{req.id}",
        subtitle="Presight R&D · AlMadar 360",
        body_text=f"**{req.requester_name}**'s **{type_label}** request needs approval {gate}.",
        facts=facts, color="warning",
        action_url=_url("/approvals"), action_label="Review Approvals",
    ))


def notify_approval_decided(req, appr) -> bool:
    """Tell the requester (and the team) whether their request was approved/rejected."""
    type_label = req.type_label() if hasattr(req, "type_label") else "Request"
    approved = getattr(appr, "status", "") == "approved"
    verb = "approved" if approved else "rejected"
    facts = [
        {"title": "Request ID", "value": f"#{req.id}"},
        {"title": "Type",       "value": type_label},
        {"title": "Decision",   "value": verb.title()},
        {"title": "By",         "value": getattr(appr, "decided_by", "") or ""},
    ]
    reason = getattr(appr, "decision_reason", "") or ""
    if reason:
        facts.append({"title": "Reason", "value": reason})
    _email_case(req, f"The request was {verb} by the approver",
                f"[AlMadar 360] Request #{req.id} {verb}",
                f"Your {type_label} request #{req.id} was {verb}"
                + (f".\n\nReason: {reason}" if reason else "."),
                facts=facts)
    return _post(_adaptive_card(
        title=f"Request #{req.id} {verb.title()}",
        subtitle="Presight R&D · AlMadar 360",
        body_text=f"The **{type_label}** request was **{verb}**"
                  + (f" — {reason}" if reason else "."),
        facts=facts, color="success" if approved else "danger",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Subscription budget alert — email the financial owner ─────────────────
def _to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _money(v, currency):
    try:
        return f"{currency}{float(v):,.0f}"
    except (TypeError, ValueError):
        return f"{currency}{v}"


def _compose_email(system: str, user: str, fb_subject: str, fb_body: str) -> tuple:
    """Run the LLM draft with the strict parser + safe fallback. Returns
    (subject, body, is_ai). Never raises — falls back to the template on any
    failure, malformed output, or when drafting is disabled/unavailable."""
    if not cfg.NOTIFY_AI_DRAFT:
        return fb_subject, fb_body, False
    try:
        import netdiag
        if not netdiag._llm_available():
            return fb_subject, fb_body, False
        parsed = _parse_draft(netdiag._llm_complete(system, user))
        if parsed:
            return parsed[0], parsed[1], True
        log.info("email draft malformed — using template")
    except Exception as exc:
        log.error("email draft failed, using template: %s", exc)
    return fb_subject, fb_body, False


# Severity metadata for budget threshold alerts (shared with budgetalerts.py).
BUDGET_SEVERITY = {
    "notify":   {"label": "Notice",   "at": 70},
    "warning":  {"label": "Warning",  "at": 80},
    "critical": {"label": "Critical", "at": 90},
}


def draft_budget_alert(sub: dict, currency: str = "$") -> dict:
    """Draft (do NOT send) an over-budget notification email to a subscription's
    financial owner. `sub` = {id, name, spend, inventory:{budget, financial_owner,
    financial_owner_email, cost_center, environment, ...}}.

    Returns {ok, to, subject, body, overage, pct, is_ai} — or {ok:False, message}
    when it isn't actually over budget or lacks the figures. The body is written by
    the LLM for this case when drafting is enabled, else a clear template.
    """
    inv = sub.get("inventory", {}) or {}
    budget = _to_float(inv.get("budget"))
    spend = sub.get("spend")
    cur = currency or "$"
    if budget is None or budget == 0:
        return {"ok": False, "message": "No monthly budget is set for this subscription."}
    if spend is None:
        return {"ok": False, "message": "Spend is unavailable for this subscription."}
    overage = round(spend - budget, 2)
    if overage <= 0:
        return {"ok": False, "message": "This subscription is within budget."}
    pct = round(spend / budget * 100)
    owner = inv.get("financial_owner") or "there"
    to = (inv.get("financial_owner_email") or "").strip()

    facts = [
        {"title": "Subscription",       "value": f"{sub.get('name')} ({sub.get('id')})"},
        {"title": "Financial owner",    "value": inv.get("financial_owner") or "—"},
        {"title": "Monthly budget",     "value": _money(budget, cur)},
        {"title": "Spend (month-to-date)", "value": _money(spend, cur)},
        {"title": "Over budget by",     "value": f"{_money(overage, cur)} ({pct}% of budget)"},
        {"title": "Cost centre",        "value": inv.get("cost_center") or "—"},
        {"title": "Environment",        "value": inv.get("environment") or "—"},
        {"title": "Technical owner",    "value": inv.get("technical_owner") or "—"},
    ]

    fb_subject = f"[Budget Alert] {sub.get('name')} is over budget by {_money(overage, cur)}"
    fb_body = (
        f"Hi {owner},\n\n"
        f"The Azure subscription \"{sub.get('name')}\" ({sub.get('id')}) has exceeded its monthly "
        f"budget. Month-to-date spend is {_money(spend, cur)} against a budget of "
        f"{_money(budget, cur)} — over by {_money(overage, cur)} ({pct}%).\n\n"
        f"Please review the recent spend and confirm whether the budget should be adjusted or "
        f"costs reduced. You can reach the technical owner "
        f"({inv.get('technical_owner') or 'n/a'}) to investigate the drivers.\n\n"
        f"Thank you,\nAlMadar 360 — Presight R&D")

    factlines = "\n".join(f"- {f['title']}: {f['value']}" for f in facts)
    system = (
        "You are AlMadar 360, the FinOps notification assistant for Presight R&D. "
        "Write a SHORT, professional email to the FINANCIAL OWNER of an Azure subscription "
        "whose month-to-date spend has exceeded its set monthly budget. "
        "Respond in ENGLISH ONLY. Do NOT think out loud, explain your reasoning, or emit "
        "any chain-of-thought / analysis / <think> tags in ANY language — output ONLY the "
        "finished email. Your FIRST characters must be the literal text 'Subject:'. "
        "Format EXACTLY: a first line 'Subject: <concise subject>', then a blank line, then "
        "the body — plain text, a brief greeting to the financial owner by name, 3-5 short "
        "sentences, and a short sign-off 'AlMadar 360 — Presight R&D'. No markdown. "
        "State the subscription, the budget, the month-to-date spend, and the overage amount "
        "and percentage; ask them to review and decide whether to adjust the budget or reduce "
        "costs. Use ONLY the facts provided — do not invent numbers or names.")
    user = (f"Financial owner: {inv.get('financial_owner') or '(unknown)'}\n"
            f"Currency symbol: {cur}\n\nFacts:\n{factlines}\n")
    subject, body, is_ai = _compose_email(system, user, fb_subject, fb_body)

    return {"ok": True, "to": to, "financial_owner": inv.get("financial_owner") or "",
            "subject": subject, "body": body, "overage": overage, "pct": pct, "is_ai": is_ai}


def draft_threshold_alert(sub: dict, assessment: dict, currency: str = "$") -> dict:
    """Draft a budget *threshold* alert (70/80/90%) to the financial owner, using
    the run-rate assessment from budgetalerts.assess(). The forecast context is
    included so the email is honest about pacing, not just the raw percentage.

    Returns {to, subject, body, is_ai}. Assumes the assessment already decided an
    email is warranted (severity set, not suppressed)."""
    inv = sub.get("inventory", {}) or {}
    cur = currency or "$"
    sev = assessment["severity"]
    label = BUDGET_SEVERITY.get(sev, {}).get("label", sev.title())
    owner = inv.get("financial_owner") or "there"
    to = (inv.get("financial_owner_email") or "").strip()
    budget = assessment["budget"]
    spend = assessment["spend"]
    raw_pct = assessment["raw_pct"]
    proj_pct = assessment["projected_pct"]
    days_left = assessment["days_left"]

    facts = [
        {"title": "Severity",           "value": f"{label} — {raw_pct}% of budget"},
        {"title": "Subscription",       "value": f"{sub.get('name')} ({sub.get('id')})"},
        {"title": "Financial owner",    "value": inv.get("financial_owner") or "—"},
        {"title": "Monthly budget",     "value": _money(budget, cur)},
        {"title": "Spend (month-to-date)", "value": f"{_money(spend, cur)} ({raw_pct}%)"},
        {"title": "Projected month-end", "value": f"{_money(assessment['projected_spend'], cur)} "
                                                  f"({proj_pct}% of budget)"},
        {"title": "Days left in month", "value": str(days_left)},
        {"title": "Cost centre",        "value": inv.get("cost_center") or "—"},
        {"title": "Technical owner",    "value": inv.get("technical_owner") or "—"},
    ]

    over_now = raw_pct >= 100
    headline = (f"has EXCEEDED its monthly budget" if over_now
                else f"has reached {raw_pct}% of its monthly budget")
    fb_subject = f"[Budget {label}] {sub.get('name')} at {raw_pct}% of budget"
    fb_body = (
        f"Hi {owner},\n\n"
        f"The Azure subscription \"{sub.get('name')}\" ({sub.get('id')}) {headline}. "
        f"Month-to-date spend is {_money(spend, cur)} against a budget of {_money(budget, cur)}. "
        f"At the current run-rate it is projected to reach {_money(assessment['projected_spend'], cur)} "
        f"({proj_pct}%) by month-end, with {days_left} day(s) remaining.\n\n"
        f"Please review the spend and decide whether to adjust the budget or reduce costs. "
        f"The technical owner ({inv.get('technical_owner') or 'n/a'}) can help investigate the drivers.\n\n"
        f"AlMadar 360 — Presight R&D")

    system = (
        "You are AlMadar 360, the FinOps notification assistant for Presight R&D. "
        f"Write a SHORT, professional '{label}'-level budget alert email to the FINANCIAL OWNER of an "
        "Azure subscription. Respond in ENGLISH ONLY. Do NOT think out loud, explain your reasoning, or "
        "emit any chain-of-thought / analysis / <think> tags in ANY language — output ONLY the finished "
        "email. Your FIRST characters must be the literal text 'Subject:'. Format EXACTLY: a first line "
        "'Subject: <concise subject>', then a blank line, then the body — plain text, a brief greeting to "
        "the owner by name, 3-5 short sentences, and a short sign-off 'AlMadar 360 — Presight R&D'. No "
        "markdown. Convey the severity proportionately (a 70% notice is informational; a 90%/over case is "
        "urgent). IMPORTANTLY, mention the projected month-end figure and days remaining so the tone matches "
        "the run-rate, not just the raw percentage. Ask them to review and decide on budget or cost action. "
        "Use ONLY the facts provided — do not invent numbers or names.")
    factlines = "\n".join(f"- {f['title']}: {f['value']}" for f in facts)
    user = (f"Alert severity: {label}\nFinancial owner: {inv.get('financial_owner') or '(unknown)'}\n"
            f"Currency symbol: {cur}\n\nFacts:\n{factlines}\n")
    subject, body, is_ai = _compose_email(system, user, fb_subject, fb_body)
    return {"to": to, "subject": subject, "body": body, "is_ai": is_ai, "facts": facts}


def send_threshold_alert(sub: dict, assessment: dict, currency: str = "$",
                         cc_corporate: bool = True) -> dict:
    """Draft + send an automatic budget threshold alert to the financial owner.
    Returns {ok, to, subject, is_ai} or {ok:False, message}."""
    draft = draft_threshold_alert(sub, assessment, currency)
    to = draft["to"]
    if not to:
        return {"ok": False, "message": "no financial-owner email set"}
    if not cfg.SMTP_HOST:
        return {"ok": False, "message": "SMTP not configured"}
    recipients = [to] + (_notify_emails() if cc_corporate else [])
    ok = _send_email(recipients, draft["subject"], draft["body"])
    return {"ok": ok, "to": to, "subject": draft["subject"], "is_ai": draft["is_ai"],
            **({} if ok else {"message": "send failed"})}


def send_budget_alert(sub: dict, currency: str = "$", subject: str = None,
                      body: str = None, cc_corporate: bool = True) -> dict:
    """Send the over-budget alert to the financial owner (optionally CC the
    corporate recipients). `subject`/`body` override the draft when the admin has
    edited them in the preview. Returns {ok, to, ...} or {ok:False, message}."""
    draft = draft_budget_alert(sub, currency)
    if not draft.get("ok"):
        return draft
    to = draft["to"]
    if not to:
        return {"ok": False, "message": "No financial-owner email is set for this subscription."}
    if not cfg.SMTP_HOST:
        return {"ok": False, "message": "SMTP is not configured (Settings → Notifications)."}
    recipients = [to] + (_notify_emails() if cc_corporate else [])
    subj = (subject or draft["subject"]).strip()
    text = (body or draft["body"]).strip()
    ok = _send_email(recipients, subj, text)
    return {"ok": ok, "to": to, "subject": subj,
            **({} if ok else {"message": "Send failed — check SMTP settings / logs."})}
