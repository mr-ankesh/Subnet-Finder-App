"""
Requester Agent — helps requesters submit CIDR requests, update statuses, and check progress.
No access to admin operations (CIDR assignment, Azure peering, firewall, UDR).
"""
import json
import logging

from config import cfg
import audit
import notifications

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Presight R&D Network Request Assistant.

You help internal teams submit and track network requests: spoke VNETs, firewall
policy changes, hub integration, ZPA routing, subnets, decommissions and DNS.

YOUR CAPABILITIES:
1. Create a new spoke VNET / CIDR request — collect details conversationally, then submit.
2. Create OTHER request types with create_service_request. Types and the details each needs:
   - firewall_policy: action (add/modify/delete), rule_kind (network/application), source,
     destination, ports_protocol, rule_name (for modify/delete), justification.
     Comma-separated lists are allowed for source and destination.
     NETWORK rules: destination is IP/CIDR(s); ports_protocol like "TCP/443, UDP/53".
     APPLICATION rules: destination must be FQDN(s) ONLY, e.g. "*.example.com, *.presight.ai"
     (Azure rejects IPs as application-rule targets — if the user gives an IP destination,
     use a network rule instead); ports_protocol like "http:8080, https:443".
   - hub_integration: subscription_id, resource_group, vnet_name, region, address_space (CIDR),
     internet_egress (bool) — for VNETs that already exist and only need hub peering
   - zpa_rnd_routing: spoke_vnet_name, spoke_cidr, spoke_udr_name, spoke_udr_rg,
     spoke_subscription_id, justification — to be routable via the ZPA R&D connector
   - zpa_other_routing: same as zpa_rnd_routing plus connector_name (required)
   - subnet_additional: vnet_name, subnet_size, subnet_purpose, existing_request_id (optional)
   - vnet_decommission: vnet_name, resource_group, subscription_id, allocated_cidr,
     created_by_admin ("yes"/"no" — was the VNET created by the admin via this portal?),
     manual_changes ("yes"/"no" — were manual changes made outside the portal?),
     manual_changes_removed (true — required when manual_changes is "yes"; if the user
     hasn't removed their manual changes yet, tell them to remove extra subnets, NSGs,
     private endpoints, attached devices and peerings first, and do NOT submit),
     confirm (must be true — always ask the user to explicitly confirm)
   - dns: dns_kind (record/private_zone_link), zone, record_type, record_name, record_value
   - other: description, priority (low/normal/high)
3. Update status to "VNET Created" — when the requester has deployed their spoke VNET.
4. Request Hub Integration for an existing VNET request — collect outbound rules + VNET details.
5. Check request status — by Request ID. Ask for the ID if not provided.
6. Send a reminder to admin — if a request is waiting too long.

WORKFLOW GUIDANCE:
- First figure out WHICH request type the user needs, then collect that type's details.
- After creating any request, always give the Request ID and remind them to note it.
- When they say their VNET is created, update to VNET_CREATED and ask if they need hub integration.
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
            "name": "create_service_request",
            "description": ("Create a non-VNET network request: firewall_policy, hub_integration, "
                            "zpa_rnd_routing, zpa_other_routing, subnet_additional, vnet_decommission, "
                            "dns, or other. Collect the type-specific details first (see system prompt)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_type":    {"type": "string",
                                        "enum": ["firewall_policy", "hub_integration", "zpa_rnd_routing",
                                                 "zpa_other_routing", "subnet_additional",
                                                 "vnet_decommission", "dns", "other"]},
                    "purpose":         {"type": "string", "description": "One-line summary of the request"},
                    "requester_name":  {"type": "string"},
                    "requester_email": {"type": "string"},
                    "details":         {"type": "object",
                                        "description": "Type-specific fields as key/value pairs"},
                },
                "required": ["request_type", "purpose", "requester_name", "details"],
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
                "Mark the spoke VNET as created (status VNET_CREATED) and save the spoke VNET "
                "details + outbound access rules needed for hub integration. Notifies admin."
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
        elif name == "create_service_request":
            return _tool_create_service_request(**inputs)
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
    # Step 1: DB write (direct sqlite3 — bypasses Flask-SQLAlchemy session)
    try:
        from db_utils import create_spoke_request, get_spoke_request
        req_id = create_spoke_request(cidr_needed, purpose, requester_name, ip_range, hub_integration)
        log.info("[requester] Request #%s committed to DB (purpose=%s, requester=%s)", req_id, purpose, requester_name)
        req = get_spoke_request(req_id)
        audit.record("request_created", actor=f"{requester_name} (via agent)", actor_role="agent",
                     request_id=req_id,
                     summary=f"New VNET request: /{cidr_needed} in {ip_range} — {str(purpose)[:100]}",
                     data={"request_type": "vnet_new", "cidr_needed": cidr_needed, "ip_range": ip_range})
    except Exception as exc:
        log.exception("[requester] DB error creating request")
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


def _tool_create_service_request(request_type, purpose, requester_name,
                                 details, requester_email=None) -> str:
    """Create a non-VNET request via the same validated path as the form API."""
    try:
        from app import _create_service_request
        result, code = _create_service_request(
            request_type=request_type, purpose=purpose, requester_name=requester_name,
            requester_email=requester_email, details=details or {},
        )
        if code != 200:
            return json.dumps(result)
        return json.dumps({**result,
                           "message": f"Request #{result['request_id']} ({request_type}) created successfully."})
    except Exception as exc:
        log.exception("[requester] error creating service request")
        return json.dumps({"error": str(exc)})


def _tool_update_vnet_created(request_id: int) -> str:
    try:
        from db_utils import get_spoke_request, update_spoke_request
        from models import RequestStatus
        req = get_spoke_request(request_id)
        if not req:
            return json.dumps({"error": f"Request #{request_id} not found."})
        if req.status != RequestStatus.CIDR_ASSIGNED:
            return json.dumps({"error": f"Cannot mark VNET Created — current status is '{req.status_label()}'. CIDR must be assigned first."})
        update_spoke_request(request_id, status=RequestStatus.VNET_CREATED)
        req = get_spoke_request(request_id)
        log.info("[requester] Request #%s → VNET_CREATED", request_id)
        audit.record("status_changed", actor=f"{req.requester_name} (via agent)", actor_role="agent",
                     request_id=request_id, summary="Status: CIDR Assigned → VNET Created",
                     data={"old": RequestStatus.CIDR_ASSIGNED, "new": RequestStatus.VNET_CREATED})
    except Exception as exc:
        log.exception("[requester] DB error updating request #%s to VNET_CREATED", request_id)
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
        from db_utils import get_spoke_request, update_spoke_request, upsert_vnet_info
        from models import RequestStatus
        req = get_spoke_request(request_id)
        if not req:
            return json.dumps({"error": f"Request #{request_id} not found."})
        if req.status not in (RequestStatus.VNET_CREATED, RequestStatus.CIDR_ASSIGNED):
            return json.dumps({"error": f"Cannot request hub integration — status is '{req.status_label()}'."})

        upsert_vnet_info(
            request_id,
            vnet_name=vnet_name,
            vnet_id=vnet_id,
            subscription_id=subscription_id,
            resource_group=resource_group,
            region=region,
            address_space=address_space,
            vpn_zpa_access=1 if vpn_zpa_access else 0,
            outbound_rules=outbound_rules,
        )
        update_spoke_request(request_id, status=RequestStatus.VNET_CREATED)
        req = get_spoke_request(request_id)
        log.info("[requester] Request #%s → VNET_CREATED (VNET details + hub request saved)", request_id)
        audit.record("vnet_info_updated", actor=f"{req.requester_name} (via agent)", actor_role="agent",
                     request_id=request_id,
                     summary=f"Hub integration requested — VNET details saved ({vnet_name or '—'})",
                     data={"vnet_name": vnet_name, "resource_group": resource_group,
                           "address_space": address_space, "vpn_zpa_access": bool(vpn_zpa_access)})
    except Exception as exc:
        log.exception("[requester] DB error on hub integration request #%s", request_id)
        return json.dumps({"error": f"Database error: {exc}"})
    try:
        notifications.notify_vnet_created(req)
    except Exception as exc:
        log.warning("[requester] Notification failed for request #%s: %s", request_id, exc)
    return json.dumps({"success": True, "message": f"Request #{request_id} updated to VNET Created. VNET details saved for hub integration. Admin notified."})


def _tool_check_status(request_id: int) -> str:
    try:
        from db_utils import get_spoke_request
        req = get_spoke_request(request_id)
        if not req:
            return json.dumps({"error": f"Request #{request_id} not found. Please check your Request ID."})
        return json.dumps(req.to_dict())
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_send_reminder(request_id: int, message: str) -> str:
    try:
        from db_utils import get_spoke_request
        req = get_spoke_request(request_id)
        if not req:
            return json.dumps({"error": f"Request #{request_id} not found."})
        ok = notifications.notify_reminder(req, message)
        if ok:
            audit.record("reminder_sent", actor=f"{req.requester_name} (via agent)", actor_role="agent",
                         request_id=request_id, summary=f"Reminder to admin: {str(message)[:150]}")
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
            kwargs = {"api_key": cfg.OPENAI_API_KEY or "not-needed", "timeout": 120}
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
            model=cfg.ANTHROPIC_MODEL, max_tokens=2048,
            # Cache the (large, static) system prompt so repeat turns only pay
            # ~0.1x for the cached prefix instead of re-billing it every call.
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS_ANTHROPIC, messages=current_messages,
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
            messages=current_messages,
        )
        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        if finish_reason == "stop" or not msg.tool_calls:
            return {"reply": msg.content or "", "tool_calls": tool_calls_log}
        if finish_reason == "tool_calls":
            # Normalise the assistant turn to a plain dict before echoing it back.
            # Appending the raw SDK message object carries null fields (refusal,
            # function_call, audio, ...) that some self-hosted OpenAI-compatible
            # servers (vLLM, LM Studio, Ollama) reject.
            current_messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name,
                                  "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })
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
