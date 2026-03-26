"""
Requester Agent — helps requesters submit CIDR requests, update statuses, and check progress.
No access to admin operations (CIDR assignment, Azure peering, firewall, UDR).
"""
import json
import logging
from datetime import datetime

from config import cfg
import notifications

log = logging.getLogger(__name__)

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Presight R&D Spoke Request Assistant.

You help internal teams submit and track Azure spoke VNET CIDR requests.

YOUR CAPABILITIES:
1. Create a new CIDR request — collect all required details conversationally, then submit.
2. Update status to "VNET Created" — when the requester has deployed their spoke VNET.
3. Request Hub Integration — collect outbound access rules and VNET details, then notify admin.
4. Check request status — by Request ID. Ask for the ID if not provided.
5. Send a reminder to admin — if a request is waiting too long.

WORKFLOW GUIDANCE:
- After creating a request, always give the Request ID and remind them to note it.
- When they say their VNET is created, update to VNET_CREATED and ask if they need hub integration.
- If they want hub integration, collect: outbound access rules, VPN/ZPA requirement, and VNET details.
- For outbound access, ask: destination (IP/FQDN), port, protocol — or confirm if they want "All open", "HTTP/HTTPS only".
- Always confirm details before submitting.
- Be friendly, concise, and guide them step by step.

WHAT YOU CANNOT DO:
- Assign CIDRs (admin only)
- Perform Azure operations (admin only)
- View all requests (admin only)
"""

# ── Tool definitions ───────────────────────────────────────────────────────

TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "create_spoke_request",
            "description": "Create a new spoke CIDR request with all collected details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cidr_needed":      {"type": "integer", "description": "CIDR prefix length, e.g. 24 for /24"},
                    "purpose":          {"type": "string"},
                    "requester_name":   {"type": "string"},
                    "ip_range":         {"type": "string", "description": "Must be '10.110.0.0/16' or '10.119.0.0/16'"},
                    "hub_integration":  {"type": "boolean", "description": "Does this spoke need hub integration?"},
                },
                "required": ["cidr_needed", "purpose", "requester_name", "ip_range", "hub_integration"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_status_vnet_created",
            "description": "Update a request status to VNET_CREATED when the requester has deployed their spoke VNET.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "integer"},
                },
                "required": ["request_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_hub_integration",
            "description": (
                "Update request to HUB_INTEGRATION_NEEDED with outbound access rules and spoke VNET details. "
                "Saves all collected information and notifies admin."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id":        {"type": "integer"},
                    "vnet_name":         {"type": "string"},
                    "vnet_id":           {"type": "string", "description": "Full ARM resource ID of the spoke VNET"},
                    "subscription_id":   {"type": "string"},
                    "resource_group":    {"type": "string"},
                    "region":            {"type": "string"},
                    "address_space":     {"type": "string", "description": "CIDR of the spoke VNET, e.g. 10.110.5.0/24"},
                    "vpn_zpa_access":    {"type": "boolean", "description": "Does this spoke need VPN or ZPA access?"},
                    "outbound_rules": {
                        "type": "array",
                        "description": "List of outbound access rules",
                        "items": {
                            "type": "object",
                            "properties": {
                                "destination": {"type": "string", "description": "IP address, FQDN, or '*' for all"},
                                "port":        {"type": "string", "description": "Port number or '*' for all"},
                                "protocol":    {"type": "string", "description": "TCP, UDP, HTTPS, HTTP, or Any"},
                            },
                        },
                    },
                },
                "required": ["request_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_request_status",
            "description": "Check the current status and details of a spoke request by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "integer"},
                },
                "required": ["request_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_reminder_to_admin",
            "description": "Send a Teams reminder notification to admin about a pending/delayed request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "integer"},
                    "message":    {"type": "string", "description": "Custom message from the requester"},
                },
                "required": ["request_id", "message"],
            },
        },
    },
]

TOOLS_ANTHROPIC = [
    {
        "name":         t["function"]["name"],
        "description":  t["function"]["description"],
        "input_schema": t["function"]["parameters"],
    }
    for t in TOOLS_OPENAI
]


# ── Tool executors ─────────────────────────────────────────────────────────

def _execute_tool(name: str, inputs: dict) -> str:
    try:
        if name == "create_spoke_request":
            return _tool_create_request(**inputs)
        elif name == "update_status_vnet_created":
            return _tool_update_vnet_created(**inputs)
        elif name == "request_hub_integration":
            return _tool_request_hub_integration(**inputs)
        elif name == "check_request_status":
            return _tool_check_status(**inputs)
        elif name == "send_reminder_to_admin":
            return _tool_send_reminder(**inputs)
        else:
            return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as exc:
        log.error("Requester tool '%s' raised: %s", name, exc)
        return json.dumps({"error": str(exc)})


def _tool_create_request(cidr_needed, purpose, requester_name, ip_range, hub_integration) -> str:
    valid_pools = ["10.110.0.0/16", "10.119.0.0/16"]
    if ip_range not in valid_pools:
        return json.dumps({"error": f"Invalid IP range. Must be one of: {valid_pools}"})
    # Step 1: DB write
    try:
        from models import db, SpokeRequest, RequestStatus
        req = SpokeRequest(
            cidr_needed=str(cidr_needed),
            purpose=purpose,
            requester_name=requester_name,
            ip_range=ip_range,
            hub_integration=bool(hub_integration),
            status=RequestStatus.CIDR_REQUESTED,
        )
        db.session.add(req)
        db.session.commit()
        req_id = req.id
        log.info("[requester] Request #%s committed to DB (purpose=%s, requester=%s)", req_id, purpose, requester_name)
    except Exception as exc:
        log.exception("[requester] DB error creating request")
        db.session.rollback()
        return json.dumps({"error": f"Database error: {exc}"})
    # Step 2: notification (best-effort, never blocks success)
    try:
        notifications.notify_cidr_requested(req)
    except Exception as exc:
        log.warning("[requester] Teams notification failed for request #%s: %s", req_id, exc)
    return json.dumps({
        "success":    True,
        "request_id": req_id,
        "message":    f"Request #{req_id} created successfully.",
    })


def _tool_update_vnet_created(request_id: int) -> str:
    try:
        from models import db, SpokeRequest, RequestStatus
        req = SpokeRequest.query.get(request_id)
        if not req:
            return json.dumps({"error": f"Request #{request_id} not found."})
        if req.status != RequestStatus.CIDR_ASSIGNED:
            return json.dumps({"error": f"Cannot mark VNET Created — current status is '{req.status_label()}'. CIDR must be assigned first."})
        req.status = RequestStatus.VNET_CREATED
        req.updated_at = datetime.utcnow()
        db.session.commit()
        log.info("[requester] Request #%s → VNET_CREATED", request_id)
    except Exception as exc:
        log.exception("[requester] DB error updating request #%s to VNET_CREATED", request_id)
        db.session.rollback()
        return json.dumps({"error": f"Database error: {exc}"})
    try:
        notifications.notify_vnet_created(req)
    except Exception as exc:
        log.warning("[requester] Notification failed for request #%s: %s", request_id, exc)
    return json.dumps({"success": True, "message": f"Request #{request_id} updated to VNET Created."})


def _tool_request_hub_integration(
    request_id: int,
    vnet_name: str = None,
    vnet_id: str = None,
    subscription_id: str = None,
    resource_group: str = None,
    region: str = None,
    address_space: str = None,
    vpn_zpa_access: bool = False,
    outbound_rules: list = None,
) -> str:
    try:
        from models import db, SpokeRequest, VnetInfo, RequestStatus
        req = SpokeRequest.query.get(request_id)
        if not req:
            return json.dumps({"error": f"Request #{request_id} not found."})
        if req.status not in (RequestStatus.VNET_CREATED, RequestStatus.CIDR_ASSIGNED):
            return json.dumps({"error": f"Cannot request hub integration — status is '{req.status_label()}'."})

        vi = req.vnet_info or VnetInfo(request_id=request_id)
        vi.vnet_name       = vnet_name or vi.vnet_name
        vi.vnet_id         = vnet_id or vi.vnet_id
        vi.subscription_id = subscription_id or vi.subscription_id
        vi.resource_group  = resource_group or vi.resource_group
        vi.region          = region or vi.region
        vi.address_space   = address_space or vi.address_space
        vi.vpn_zpa_access  = bool(vpn_zpa_access)
        if outbound_rules is not None:
            vi.set_outbound_rules(outbound_rules)

        if not req.vnet_info:
            db.session.add(vi)

        req.status = RequestStatus.HUB_INTEGRATION_NEEDED
        req.updated_at = datetime.utcnow()
        db.session.commit()
        log.info("[requester] Request #%s → HUB_INTEGRATION_NEEDED", request_id)
    except Exception as exc:
        log.exception("[requester] DB error on hub integration request #%s", request_id)
        db.session.rollback()
        return json.dumps({"error": f"Database error: {exc}"})
    try:
        notifications.notify_hub_integration_needed(req)
    except Exception as exc:
        log.warning("[requester] Notification failed for request #%s: %s", request_id, exc)
    return json.dumps({"success": True, "message": f"Request #{request_id} updated to Hub Integration Needed. VNET details saved. Admin notified."})


def _tool_check_status(request_id: int) -> str:
    try:
        from models import SpokeRequest
        req = SpokeRequest.query.get(request_id)
        if not req:
            return json.dumps({"error": f"Request #{request_id} not found. Please check your Request ID."})
        data = req.to_dict()
        if req.vnet_info:
            data["vnet_info"] = req.vnet_info.to_dict()
        return json.dumps(data)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_send_reminder(request_id: int, message: str) -> str:
    try:
        from models import SpokeRequest
        req = SpokeRequest.query.get(request_id)
        if not req:
            return json.dumps({"error": f"Request #{request_id} not found."})
        ok = notifications.notify_reminder(req, message)
        return json.dumps({"success": ok, "message": "Reminder sent to admin via Teams." if ok else "Notification failed — check TEAMS_WEBHOOK_URL."})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ── Client factory (shared with admin agent) ───────────────────────────────

_client = None

def _get_client():
    global _client
    if _client is not None:
        return _client
    provider = cfg.AGENT_PROVIDER.lower()
    if provider == "anthropic":
        import anthropic
        _client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
    elif provider == "openai":
        from openai import AzureOpenAI, OpenAI
        if cfg.OPENAI_BASE_URL and "azure.com" in cfg.OPENAI_BASE_URL:
            _client = AzureOpenAI(azure_endpoint=cfg.OPENAI_BASE_URL, api_key=cfg.OPENAI_API_KEY, api_version=cfg.OPENAI_API_VERSION)
        else:
            kwargs = {"api_key": cfg.OPENAI_API_KEY or "not-needed"}
            if cfg.OPENAI_BASE_URL:
                kwargs["base_url"] = cfg.OPENAI_BASE_URL
            _client = OpenAI(**kwargs)
    else:
        raise RuntimeError(f"Unknown AGENT_PROVIDER '{provider}'.")
    return _client


# ── Main chat function ─────────────────────────────────────────────────────

def chat(messages: list, max_iterations: int = 10) -> dict:
    provider = cfg.AGENT_PROVIDER.lower()
    return _chat_anthropic(messages, max_iterations) if provider == "anthropic" else _chat_openai(messages, max_iterations)


def _chat_anthropic(messages, max_iterations):
    client = _get_client()
    tool_calls_log = []
    current_messages = list(messages)

    for _ in range(max_iterations):
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=2048,
            system=SYSTEM_PROMPT, tools=TOOLS_ANTHROPIC, messages=current_messages,
        )
        if response.stop_reason == "end_turn":
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            return {"reply": text, "tool_calls": tool_calls_log}
        if response.stop_reason == "tool_use":
            assistant_content, tool_results = [], []
            for block in response.content:
                if hasattr(block, "text"):
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
                    result_str = _execute_tool(block.name, dict(block.input))
                    tool_calls_log.append({"tool": block.name, "input": block.input, "result": result_str, "status": "done"})
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result_str})
            current_messages.append({"role": "assistant", "content": assistant_content})
            current_messages.append({"role": "user", "content": tool_results})
            continue
        break
    return {"reply": "Reached maximum steps. Please try again.", "tool_calls": tool_calls_log}


def _chat_openai(messages, max_iterations):
    client = _get_client()
    tool_calls_log = []
    current_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(messages)

    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=cfg.OPENAI_MODEL, tools=TOOLS_OPENAI,
            tool_choice="auto", messages=current_messages,
        )
        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        if finish_reason == "stop" or not msg.tool_calls:
            return {"reply": msg.content or "", "tool_calls": tool_calls_log}
        if finish_reason == "tool_calls":
            current_messages.append(msg)
            for tc in msg.tool_calls:
                try:
                    inputs = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    inputs = {}
                result_str = _execute_tool(tc.function.name, inputs)
                tool_calls_log.append({"tool": tc.function.name, "input": inputs, "result": result_str, "status": "done"})
                current_messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})
            continue
        break
    return {"reply": "Reached maximum steps. Please try again.", "tool_calls": tool_calls_log}
