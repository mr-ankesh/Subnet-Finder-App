"""
Teams notification helpers — Power Automate Workflows webhook (Adaptive Card format).
"""
import json
import logging
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


# ── Email (SMTP) — direct-to-requester notifications ──────────────────────
def _send_email(to_addr: str, subject: str, body_text: str) -> bool:
    if not cfg.SMTP_HOST:
        log.info("SMTP_HOST not set — email to %s skipped.", to_addr)
        return False
    if not to_addr:
        return False
    try:
        msg = EmailMessage()
        msg["From"] = cfg.SMTP_FROM or cfg.SMTP_USER or "noreply@localhost"
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(body_text)
        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=15) as s:
            if cfg.SMTP_USE_TLS:
                s.starttls()
            if cfg.SMTP_USER:
                s.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            s.send_message(msg)
        log.info("Email sent to %s: %s", to_addr, subject)
        return True
    except Exception as exc:
        log.error("Email to %s failed: %s", to_addr, exc)
        return False


def _email_requester(req, subject: str, body_text: str) -> bool:
    """Best-effort email to the request's requester (no-op if no email/SMTP)."""
    to_addr = getattr(req, "requester_email", None)
    if not to_addr:
        return False
    link = _url(f"/requests/{req.id}") or ""
    footer = f"\n\nTrack your request: {link}" if link else ""
    return _send_email(to_addr, subject, body_text + footer)


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
    _email_requester(req, f"[Network Copilot] {type_label} request received — #{req.id}",
                     f"Hi {req.requester_name},\n\nYour {type_label} request #{req.id} has been "
                     f"submitted and is awaiting admin review.\n\nSummary: {req.purpose}")
    return _post(_adaptive_card(
        title=f"New {type_label} Request #{req.id}",
        subtitle="Presight R&D · Network Copilot",
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
    _email_requester(req, f"[Network Copilot] Request #{req.id} → {status_label}",
                     f"Hi {req.requester_name},\n\nYour {type_label} request #{req.id} "
                     f"is now: {status_label}.")
    return _post(_adaptive_card(
        title=f"Request #{req.id} ({type_label}) → {status_label}",
        subtitle="Presight R&D · Network Copilot",
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
    return _post(_adaptive_card(
        title=f"New CIDR Request #{req.id}",
        subtitle="Presight R&D · Network Copilot",
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
    _email_requester(req, f"[Network Copilot] CIDR {subnet} assigned — Request #{req.id}",
                     f"Hi {req.requester_name},\n\nYour spoke CIDR request #{req.id} has been "
                     f"assigned the subnet {subnet}. You can now deploy your spoke VNET.")
    return _post(_adaptive_card(
        title=f"CIDR Assigned — Request #{req.id}",
        subtitle="Presight R&D · Network Copilot",
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
    _email_requester(req, f"[Network Copilot] VNET created — Request #{req.id}",
                     f"Hi {req.requester_name},\n\nYour spoke VNET for request #{req.id} "
                     f"(subnet {req.allocated_subnet or '—'}) has been created.")
    return _post(_adaptive_card(
        title=f"VNET Created — Request #{req.id}",
        subtitle="Presight R&D · Network Copilot",
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
    return _post(_adaptive_card(
        title=f"Hub Integration Needed — Request #{req.id}",
        subtitle="Presight R&D · Network Copilot",
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
    _email_requester(req, f"[Network Copilot] Hub integration started — Request #{req.id}",
                     f"Hi {req.requester_name},\n\nHub integration for your spoke VNET "
                     f"(request #{req.id}) has started.")
    return _post(_adaptive_card(
        title=f"Hub Integration In Progress — Request #{req.id}",
        subtitle="Presight R&D · Network Copilot",
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
    _email_requester(req, f"[Network Copilot] Request #{req.id} complete — hub integrated",
                     f"Hi {req.requester_name},\n\nYour spoke VNET (request #{req.id}, subnet "
                     f"{getattr(req, 'allocated_subnet', None) or '—'}) is fully integrated with the hub. "
                     f"Onboarding is complete.")
    return _post(_adaptive_card(
        title=f"Hub Integration Complete — Request #{req.id}",
        subtitle="Presight R&D · Network Copilot",
        body_text=f"All hub integration tasks have been completed successfully.{action_text}",
        facts=facts, color="success",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Generic / reminder ────────────────────────────────────────────────────
def notify_custom(title: str, message: str, level: str = "info") -> bool:
    return _post(_adaptive_card(
        title=title,
        subtitle="Presight R&D · Network Copilot",
        body_text=message, facts=[], color=level,
    ))


def notify_reminder(req, message: str) -> bool:
    facts = [
        {"title": "Request ID", "value": f"#{req.id}"},
        {"title": "Requester",  "value": req.requester_name},
        {"title": "Status",     "value": req.status_label()},
        {"title": "Message",    "value": message},
    ]
    return _post(_adaptive_card(
        title=f"Reminder — Request #{req.id}",
        subtitle="Presight R&D · Network Copilot",
        body_text=f"**{req.requester_name}** is following up on their request.",
        facts=facts, color="warning",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))
