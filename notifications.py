"""
Teams notification helpers.
Uses the new Power Automate Workflows webhook (Adaptive Card format).

How to get your webhook URL:
  1. In Teams channel → Workflows → "Post to a channel when a webhook request is received"
  2. Copy the generated HTTP POST URL
  3. Set TEAMS_WEBHOOK_URL=<that URL> in your .env
"""
import json
import logging
import requests as http_requests
from config import cfg

log = logging.getLogger(__name__)

# Accent colours (hex without #)
COLOR = {
    "info":    "0078D4",
    "success": "107C10",
    "warning": "FFB900",
    "danger":  "D13438",
}

# Map color key → emoji prefix for the header
ICON = {
    "info":    "🔵",
    "success": "✅",
    "warning": "🟡",
    "danger":  "🔴",
}


def _adaptive_card(title: str, subtitle: str, body_text: str,
                   facts: list[dict], color: str = "info",
                   action_url: str = None, action_label: str = "View") -> dict:
    """
    Build a Teams-compatible Adaptive Card payload for the Workflows webhook.

    facts: list of {"title": ..., "value": ...}
    """
    accent = COLOR.get(color, COLOR["info"])
    icon   = ICON.get(color, "🔵")

    card_body = [
        {
            "type":   "TextBlock",
            "text":   f"{icon} {title}",
            "weight": "Bolder",
            "size":   "Medium",
            "wrap":   True,
            "color":  "Accent",
        },
        {
            "type":  "TextBlock",
            "text":  subtitle,
            "size":  "Small",
            "color": "Subtle",
            "wrap":  True,
            "spacing": "None",
        },
    ]

    if body_text:
        card_body.append({
            "type":    "TextBlock",
            "text":    body_text,
            "wrap":    True,
            "spacing": "Medium",
        })

    if facts:
        card_body.append({
            "type":   "FactSet",
            "facts":  facts,
            "spacing": "Medium",
        })

    card_actions = []
    if action_url:
        card_actions.append({
            "type":  "Action.OpenUrl",
            "title": action_label,
            "url":   action_url,
        })

    adaptive_card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type":    "AdaptiveCard",
        "version": "1.5",
        "body":    card_body,
    }
    if card_actions:
        adaptive_card["actions"] = card_actions

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl":  None,
                "content":     adaptive_card,
            }
        ],
    }


def _post(payload: dict) -> bool:
    """POST an Adaptive Card payload to the Workflows webhook. Returns True on success."""
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
            log.error("Teams webhook returned %s: %s", resp.status_code, resp.text[:300])
            return False
        return True
    except Exception as exc:
        log.error("Failed to post Teams notification: %s", exc)
        return False


def notify_new_request(req) -> bool:
    """Step 1 — alert that a new spoke request was submitted."""
    facts = [
        {"title": "Request ID",      "value": f"#{req.id}"},
        {"title": "Requester",       "value": req.requester_name},
        {"title": "CIDR Needed",     "value": f"/{req.cidr_needed}"},
        {"title": "IP Range Pool",   "value": req.ip_range},
        {"title": "Purpose",         "value": req.purpose},
        {"title": "Hub Integration", "value": "Yes" if req.hub_integration else "No"},
        {"title": "Submitted",       "value": req.created_at.strftime("%Y-%m-%d %H:%M UTC") if req.created_at else "—"},
    ]
    payload = _adaptive_card(
        title      = f"New Spoke Request #{req.id}",
        subtitle   = "Presight R&D Azure Subnet Manager",
        body_text  = f"A new spoke VNET request has been submitted by **{req.requester_name}** and is awaiting subnet allocation.",
        facts      = facts,
        color      = "info",
        action_url = f"{cfg.SUBNET_FINDER_BASE_URL}/requests/{req.id}",
        action_label = "View Request",
    )
    return _post(payload)


def notify_subnet_allocated(req, subnet: str) -> bool:
    """Step 2 — subnet was allocated by the agent."""
    facts = [
        {"title": "Request ID",       "value": f"#{req.id}"},
        {"title": "Requester",        "value": req.requester_name},
        {"title": "Allocated Subnet", "value": subnet},
        {"title": "Pool",             "value": req.ip_range},
        {"title": "Purpose",          "value": req.purpose},
        {"title": "Hub Integration",  "value": "Yes — awaiting spoke deployment" if req.hub_integration else "No — complete"},
    ]
    body = f"Subnet **{subnet}** has been allocated for request #{req.id}."
    if req.hub_integration:
        body += " Please deploy your spoke VNET, then mark the request as Completed."
    payload = _adaptive_card(
        title      = f"Subnet Allocated — Request #{req.id}",
        subtitle   = "Presight R&D Azure Subnet Manager",
        body_text  = body,
        facts      = facts,
        color      = "success",
        action_url = f"{cfg.SUBNET_FINDER_BASE_URL}/requests/{req.id}",
        action_label = "View Request",
    )
    return _post(payload)


def notify_deployment_completed(req) -> bool:
    """Step 3 — requester marked deployment as Completed."""
    facts = [
        {"title": "Request ID", "value": f"#{req.id}"},
        {"title": "Requester",  "value": req.requester_name},
        {"title": "Subnet",     "value": req.allocated_subnet or "—"},
        {"title": "Purpose",    "value": req.purpose},
    ]
    payload = _adaptive_card(
        title      = f"Spoke Deployment Completed — Request #{req.id}",
        subtitle   = "Presight R&D Azure Subnet Manager",
        body_text  = "The requester has marked spoke deployment as completed. Please fill in the VNET info form so hub integration can proceed.",
        facts      = facts,
        color      = "warning",
        action_url = f"{cfg.SUBNET_FINDER_BASE_URL}/requests/{req.id}/vnet-info",
        action_label = "Fill VNET Info",
    )
    return _post(payload)


def notify_hub_integration_done(req, actions_taken: list) -> bool:
    """Step 4 — agent completed hub integration tasks."""
    facts = [
        {"title": "Request ID", "value": f"#{req.id}"},
        {"title": "Requester",  "value": req.requester_name},
        {"title": "Subnet",     "value": req.allocated_subnet or "—"},
    ]
    action_text = "  \n".join(f"• {a}" for a in actions_taken) if actions_taken else "No actions recorded."
    payload = _adaptive_card(
        title      = f"Hub Integration Completed — Request #{req.id}",
        subtitle   = "Presight R&D Azure Subnet Manager",
        body_text  = f"All hub integration tasks have been executed by the automation agent.\n\n{action_text}",
        facts      = facts,
        color      = "success",
        action_url = f"{cfg.SUBNET_FINDER_BASE_URL}/requests/{req.id}",
        action_label = "View Request",
    )
    return _post(payload)


def notify_custom(title: str, message: str, level: str = "info") -> bool:
    """Generic notification — used by the agent for freeform messages."""
    payload = _adaptive_card(
        title     = title,
        subtitle  = "Presight R&D Azure Subnet Manager",
        body_text = message,
        facts     = [],
        color     = level,
    )
    return _post(payload)
