"""
Teams notification helpers — Power Automate Workflows webhook (Adaptive Card format).
"""
import json
import logging
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


def _url(path: str) -> str:
    return f"{cfg.SUBNET_FINDER_BASE_URL.rstrip('/')}{path}"


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
        subtitle="Presight R&D Azure Subnet Manager",
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
    return _post(_adaptive_card(
        title=f"CIDR Assigned — Request #{req.id}",
        subtitle="Presight R&D Azure Subnet Manager",
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
    return _post(_adaptive_card(
        title=f"VNET Created — Request #{req.id}",
        subtitle="Presight R&D Azure Subnet Manager",
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
        subtitle="Presight R&D Azure Subnet Manager",
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
    return _post(_adaptive_card(
        title=f"Hub Integration In Progress — Request #{req.id}",
        subtitle="Presight R&D Azure Subnet Manager",
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
    return _post(_adaptive_card(
        title=f"Hub Integration Complete — Request #{req.id}",
        subtitle="Presight R&D Azure Subnet Manager",
        body_text=f"All hub integration tasks have been completed successfully.{action_text}",
        facts=facts, color="success",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))


# ── Generic / reminder ────────────────────────────────────────────────────
def notify_custom(title: str, message: str, level: str = "info") -> bool:
    return _post(_adaptive_card(
        title=title,
        subtitle="Presight R&D Azure Subnet Manager",
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
        subtitle="Presight R&D Azure Subnet Manager",
        body_text=f"**{req.requester_name}** is following up on their request.",
        facts=facts, color="warning",
        action_url=_url(f"/requests/{req.id}"), action_label="View Request",
    ))
