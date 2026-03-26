"""
Teams notification helpers.
Uses the Incoming Webhook (MessageCard / Adaptive Card format).
"""
import json
import logging
import requests as http_requests
from config import cfg

log = logging.getLogger(__name__)

# ── Color palette ──────────────────────────────────────────────────────────
COLOR = {
    "info":    "0078D4",   # Teams blue
    "success": "107C10",   # green
    "warning": "FFB900",   # amber
    "danger":  "D13438",   # red
}


def _post(payload: dict) -> bool:
    """POST a MessageCard payload to the configured webhook. Returns True on success."""
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
            log.error("Teams webhook returned %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as exc:
        log.error("Failed to post Teams notification: %s", exc)
        return False


def notify_new_request(req) -> bool:
    """Step 1 — alert that a new spoke request was submitted."""
    facts = [
        {"name": "Request ID",       "value": f"#{req.id}"},
        {"name": "Requester",        "value": req.requester_name},
        {"name": "CIDR Needed",      "value": req.cidr_needed},
        {"name": "IP Range Pool",    "value": req.ip_range},
        {"name": "Purpose",          "value": req.purpose},
        {"name": "Hub Integration",  "value": "Yes ✅" if req.hub_integration else "No"},
        {"name": "Submitted",        "value": req.created_at.strftime("%Y-%m-%d %H:%M UTC") if req.created_at else "—"},
    ]
    payload = {
        "@type":       "MessageCard",
        "@context":    "https://schema.org/extensions",
        "themeColor":  COLOR["info"],
        "summary":     f"New Spoke Request #{req.id} from {req.requester_name}",
        "sections": [{
            "activityTitle":    "🆕 New Spoke Request Submitted",
            "activitySubtitle": f"Presight R&D Azure Subnet Manager",
            "activityText":     f"A new spoke VNET request has been submitted and is awaiting subnet allocation.",
            "facts":            facts,
        }],
        "potentialAction": [{
            "@type": "OpenUri",
            "name":  "View Request",
            "targets": [{"os": "default", "uri": f"{cfg.SUBNET_FINDER_BASE_URL}/requests/{req.id}"}],
        }],
    }
    return _post(payload)


def notify_subnet_allocated(req, subnet: str) -> bool:
    """Step 2 — subnet was allocated by the agent."""
    pool_key = req.ip_range.rsplit(".", 2)[0] if req.ip_range else ""
    facts = [
        {"name": "Request ID",      "value": f"#{req.id}"},
        {"name": "Requester",       "value": req.requester_name},
        {"name": "Allocated Subnet","value": subnet},
        {"name": "Pool",            "value": req.ip_range},
        {"name": "Purpose",         "value": req.purpose},
        {"name": "Hub Integration", "value": "Yes — awaiting spoke deployment" if req.hub_integration else "No — done"},
    ]
    payload = {
        "@type":      "MessageCard",
        "@context":   "https://schema.org/extensions",
        "themeColor": COLOR["success"],
        "summary":    f"Subnet {subnet} allocated for Request #{req.id}",
        "sections": [{
            "activityTitle":    "✅ Subnet Allocated",
            "activitySubtitle": "Presight R&D Azure Subnet Manager",
            "activityText":     (
                f"Subnet **{subnet}** has been allocated for request #{req.id}."
                + (" Please deploy your spoke VNET and then mark the request as Completed." if req.hub_integration else "")
            ),
            "facts": facts,
        }],
        "potentialAction": [{
            "@type": "OpenUri",
            "name":  "View Request",
            "targets": [{"os": "default", "uri": f"{cfg.SUBNET_FINDER_BASE_URL}/requests/{req.id}"}],
        }],
    }
    return _post(payload)


def notify_deployment_completed(req) -> bool:
    """Step 3 — requester marked deployment as Completed."""
    facts = [
        {"name": "Request ID",  "value": f"#{req.id}"},
        {"name": "Requester",   "value": req.requester_name},
        {"name": "Subnet",      "value": req.allocated_subnet or "—"},
        {"name": "Purpose",     "value": req.purpose},
    ]
    payload = {
        "@type":      "MessageCard",
        "@context":   "https://schema.org/extensions",
        "themeColor": COLOR["warning"],
        "summary":    f"Spoke deployment completed for Request #{req.id}",
        "sections": [{
            "activityTitle":    "🚀 Spoke Deployment Completed",
            "activitySubtitle": "Presight R&D Azure Subnet Manager",
            "activityText":     "The requester has marked spoke deployment as completed. Please fill in the VNET info form so hub integration can proceed.",
            "facts":            facts,
        }],
        "potentialAction": [{
            "@type": "OpenUri",
            "name":  "Fill VNET Info",
            "targets": [{"os": "default", "uri": f"{cfg.SUBNET_FINDER_BASE_URL}/requests/{req.id}/vnet-info"}],
        }],
    }
    return _post(payload)


def notify_hub_integration_done(req, actions_taken: list) -> bool:
    """Step 4 — agent completed hub integration tasks."""
    action_text = "\n".join(f"• {a}" for a in actions_taken) if actions_taken else "No actions recorded."
    facts = [
        {"name": "Request ID", "value": f"#{req.id}"},
        {"name": "Requester",  "value": req.requester_name},
        {"name": "Subnet",     "value": req.allocated_subnet or "—"},
        {"name": "Actions",    "value": action_text},
    ]
    payload = {
        "@type":      "MessageCard",
        "@context":   "https://schema.org/extensions",
        "themeColor": COLOR["success"],
        "summary":    f"Hub integration complete for Request #{req.id}",
        "sections": [{
            "activityTitle":    "🔗 Hub Integration Completed",
            "activitySubtitle": "Presight R&D Azure Subnet Manager",
            "activityText":     "All hub integration tasks have been executed by the automation agent.",
            "facts":            facts,
        }],
    }
    return _post(payload)


def notify_custom(title: str, message: str, level: str = "info") -> bool:
    """Generic notification — used by the agent for freeform messages."""
    payload = {
        "@type":      "MessageCard",
        "@context":   "https://schema.org/extensions",
        "themeColor": COLOR.get(level, COLOR["info"]),
        "summary":    title,
        "sections": [{
            "activityTitle": title,
            "activityText":  message,
        }],
    }
    return _post(payload)
