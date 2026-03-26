"""
Claude-powered automation agent.
Handles Part 2: subnet finding (Step 2) and Azure operations (Step 4).

Tool use loop:
  1. User sends a message to /api/agent/chat
  2. We call Claude with the tool list
  3. If stop_reason == "tool_use" → execute tool(s) → feed results back → repeat
  4. When stop_reason == "end_turn" → return final text to user
"""
import json
import logging
import requests as http_requests
import anthropic

from config import cfg
import azure_tools
import notifications

log = logging.getLogger(__name__)

# ── Anthropic client (lazy — only needs key at runtime) ───────────────────
_client = None

def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
    return _client


# ── System prompt ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are the Presight R&D Azure Network Automation Agent.

Your job is to help the network team manage Azure spoke VNETs end-to-end:

PART 2 — STEP 2 (Subnet Allocation):
- When asked to find a subnet, use find_subnets to look up available subnets from the subnet finder.
- Present the options clearly to the user.
- When the user picks one, use allocate_subnet to allocate it and update the request record.
- Then send a Teams notification.

PART 2 — STEP 4 (Hub Integration — keyword/option driven):
You understand these keywords and take the corresponding action:
  "peer hub"         → use peer_hub_vnet to create VNET peering
  "check udr"        → use check_udr to validate routes
  "firewall rule"    → use add_firewall_rule to add outbound rules
  "add route"        → use add_udr_routes to insert routes into both UDRs

Always:
- Be concise and clear about what you did and the result.
- If an action succeeded, confirm it and send a Teams notification.
- If something fails, explain the error and suggest a fix.
- Ask for clarification if required parameters are missing.
- When doing Step 4 tasks, pull the VNET info from the database using get_request first.

Azure environment:
- Hub VNET: {hub_vnet} in resource group {hub_rg} (subscription {hub_sub})
- UDR 1: {udr1} | UDR 2: {udr2} (RG: {udr_rg})
- Firewall Policy: {fw_policy} / Rule Collection Group: {fw_rcg}
""".format(
    hub_vnet=cfg.HUB_VNET_NAME       or "<HUB_VNET_NAME not set>",
    hub_rg=cfg.HUB_RESOURCE_GROUP    or "<HUB_RESOURCE_GROUP not set>",
    hub_sub=cfg.HUB_SUBSCRIPTION_ID  or "<HUB_SUBSCRIPTION_ID not set>",
    udr1=cfg.UDR_NAME_1              or "<UDR_NAME_1 not set>",
    udr2=cfg.UDR_NAME_2              or "<UDR_NAME_2 not set>",
    udr_rg=cfg.UDR_RESOURCE_GROUP    or "<UDR_RESOURCE_GROUP not set>",
    fw_policy=cfg.FIREWALL_POLICY_NAME            or "<FIREWALL_POLICY_NAME not set>",
    fw_rcg=cfg.FIREWALL_RULE_COLLECTION_GROUP     or "<FIREWALL_RULE_COLLECTION_GROUP not set>",
)


# ── Tool definitions ───────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "find_subnets",
        "description": (
            "Find available subnets in the Presight subnet finder app for a given pool and prefix length. "
            "Returns a list of available CIDR blocks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pool":   {"type": "string", "description": "Pool key: '10.110' or '10.119'"},
                "prefix": {"type": "integer", "description": "CIDR prefix length, e.g. 24 for /24"},
            },
            "required": ["pool", "prefix"],
        },
    },
    {
        "name": "allocate_subnet",
        "description": "Allocate a specific subnet in the subnet finder app and update the spoke request record.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pool":         {"type": "string",  "description": "Pool key: '10.110' or '10.119'"},
                "subnet":       {"type": "string",  "description": "CIDR to allocate, e.g. '10.110.5.0/24'"},
                "purpose":      {"type": "string",  "description": "Purpose / description of the subnet"},
                "requested_by": {"type": "string",  "description": "Name of the requester"},
                "allocated_by": {"type": "string",  "description": "Who is allocating (usually 'Automation Agent')"},
                "request_id":   {"type": "integer", "description": "Spoke request DB ID to update (optional)"},
            },
            "required": ["pool", "subnet", "purpose", "requested_by", "allocated_by"],
        },
    },
    {
        "name": "get_request",
        "description": "Fetch a spoke request and its VNET info from the database by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer", "description": "Spoke request ID"},
            },
            "required": ["request_id"],
        },
    },
    {
        "name": "peer_hub_vnet",
        "description": "Create VNET peering between a spoke VNET and the hub VNET in both directions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spoke_subscription_id": {"type": "string"},
                "spoke_resource_group":  {"type": "string"},
                "spoke_vnet_name":       {"type": "string"},
                "spoke_address_space":   {"type": "string", "description": "CIDR of the spoke VNET, e.g. '10.110.5.0/24'"},
            },
            "required": ["spoke_subscription_id", "spoke_resource_group", "spoke_vnet_name", "spoke_address_space"],
        },
    },
    {
        "name": "check_udr",
        "description": "Check whether a required route prefix exists in a UDR table.",
        "input_schema": {
            "type": "object",
            "properties": {
                "udr_name":                {"type": "string", "description": "Name of the UDR route table"},
                "udr_resource_group":      {"type": "string", "description": "Resource group of the UDR (defaults to UDR_RESOURCE_GROUP env var)"},
                "required_address_prefix": {"type": "string", "description": "CIDR to check for, e.g. '10.110.5.0/24'"},
            },
            "required": ["udr_name", "required_address_prefix"],
        },
    },
    {
        "name": "add_firewall_rule",
        "description": "Add an outbound network rule to the Azure Firewall policy.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rule_name":               {"type": "string"},
                "destination_addresses":   {"type": "array",  "items": {"type": "string"}, "description": "List of destination IPs or FQDNs"},
                "destination_ports":       {"type": "array",  "items": {"type": "string"}, "description": "List of ports, e.g. ['443', '80']"},
                "protocol":                {"type": "string", "description": "TCP or UDP", "default": "TCP"},
                "source_addresses":        {"type": "array",  "items": {"type": "string"}, "description": "Source IPs (default: ['*'])"},
            },
            "required": ["rule_name", "destination_addresses", "destination_ports"],
        },
    },
    {
        "name": "add_udr_routes",
        "description": "Add a route to both configured UDR tables simultaneously.",
        "input_schema": {
            "type": "object",
            "properties": {
                "route_name":     {"type": "string"},
                "address_prefix": {"type": "string", "description": "CIDR prefix for the route, e.g. '10.110.5.0/24'"},
                "next_hop_type":  {"type": "string", "description": "VirtualAppliance | VnetLocal | Internet | None"},
                "next_hop_ip":    {"type": "string", "description": "Next hop IP (required if next_hop_type is VirtualAppliance)"},
            },
            "required": ["route_name", "address_prefix", "next_hop_type"],
        },
    },
    {
        "name": "send_notification",
        "description": "Send a custom Teams notification message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":   {"type": "string"},
                "message": {"type": "string"},
                "level":   {"type": "string", "description": "info | success | warning | danger", "default": "info"},
            },
            "required": ["title", "message"],
        },
    },
]


# ── Tool executor ──────────────────────────────────────────────────────────

def _execute_tool(name: str, inputs: dict) -> str:
    """Run a tool and return a JSON string result."""
    try:
        if name == "find_subnets":
            return _tool_find_subnets(**inputs)
        elif name == "allocate_subnet":
            return _tool_allocate_subnet(**inputs)
        elif name == "get_request":
            return _tool_get_request(**inputs)
        elif name == "peer_hub_vnet":
            result = azure_tools.peer_hub_vnet(**inputs)
            return json.dumps(result)
        elif name == "check_udr":
            rg = inputs.pop("udr_resource_group", cfg.UDR_RESOURCE_GROUP)
            result = azure_tools.check_udr(udr_resource_group=rg, **inputs)
            return json.dumps(result)
        elif name == "add_firewall_rule":
            result = azure_tools.add_firewall_rule(**inputs)
            return json.dumps(result)
        elif name == "add_udr_routes":
            result = azure_tools.add_udr_routes(**inputs)
            return json.dumps(result)
        elif name == "send_notification":
            ok = notifications.notify_custom(
                title=inputs.get("title", "Agent Notification"),
                message=inputs.get("message", ""),
                level=inputs.get("level", "info"),
            )
            return json.dumps({"success": ok})
        else:
            return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as exc:
        log.error("Tool '%s' raised: %s", name, exc)
        return json.dumps({"error": str(exc)})


def _tool_find_subnets(pool: str, prefix: int) -> str:
    auth = (cfg.SUBNET_FINDER_USER, cfg.SUBNET_FINDER_PASS) if cfg.SUBNET_FINDER_USER else None
    try:
        resp = http_requests.post(
            f"{cfg.SUBNET_FINDER_BASE_URL}/get_subnet",
            params={"pool": pool},
            data={"cidr": f"/{prefix}", "pool": pool},
            auth=auth,
            timeout=15,
            verify=False,
        )
        data = resp.json()
        return json.dumps(data)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_allocate_subnet(
    pool: str, subnet: str, purpose: str,
    requested_by: str, allocated_by: str,
    request_id: int = None,
) -> str:
    auth = (cfg.SUBNET_FINDER_USER, cfg.SUBNET_FINDER_PASS) if cfg.SUBNET_FINDER_USER else None
    try:
        resp = http_requests.post(
            f"{cfg.SUBNET_FINDER_BASE_URL}/allocate",
            params={"pool": pool},
            data={
                "pool": pool, "selected": subnet,
                "purpose": purpose, "requested_by": requested_by,
                "allocated_by": allocated_by,
            },
            auth=auth,
            timeout=15,
            verify=False,
        )
        data = resp.json()
        # Update DB record if request_id provided
        if request_id and data.get("message") and "error" not in data:
            try:
                from models import db, SpokeRequest, RequestStatus
                from datetime import datetime
                req = SpokeRequest.query.get(request_id)
                if req:
                    req.allocated_subnet = subnet
                    req.status = RequestStatus.SUBNET_ALLOCATED
                    req.updated_at = datetime.utcnow()
                    db.session.commit()
                    notifications.notify_subnet_allocated(req, subnet)
            except Exception as db_exc:
                log.error("DB update after allocation failed: %s", db_exc)
        return json.dumps(data)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_get_request(request_id: int) -> str:
    try:
        from models import SpokeRequest
        req = SpokeRequest.query.get(request_id)
        if not req:
            return json.dumps({"error": f"Request #{request_id} not found"})
        result = req.to_dict()
        if req.vnet_info:
            result["vnet_info"] = req.vnet_info.to_dict()
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ── Main chat function ─────────────────────────────────────────────────────

def chat(messages: list, max_iterations: int = 10) -> dict:
    """
    Run the agentic loop.
    messages: list of {"role": "user"/"assistant", "content": str}
    Returns: {"reply": str, "tool_calls": [...]}
    """
    client = _get_client()
    tool_calls_log = []
    current_messages = list(messages)

    for iteration in range(max_iterations):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=current_messages,
        )

        if response.stop_reason == "end_turn":
            # Extract final text
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
            return {"reply": text, "tool_calls": tool_calls_log}

        if response.stop_reason == "tool_use":
            # Build assistant message with all content blocks
            assistant_content = []
            tool_results = []

            for block in response.content:
                if hasattr(block, "text"):
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id":   block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                    log.info("Agent calling tool: %s(%s)", block.name, block.input)
                    result_str = _execute_tool(block.name, dict(block.input))
                    tool_calls_log.append({
                        "tool":   block.name,
                        "input":  block.input,
                        "result": result_str,
                    })
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     result_str,
                    })

            current_messages.append({"role": "assistant", "content": assistant_content})
            current_messages.append({"role": "user",      "content": tool_results})
            continue

        # Unexpected stop reason
        log.warning("Unexpected stop_reason: %s", response.stop_reason)
        break

    return {"reply": "Agent reached maximum iterations without a final answer.", "tool_calls": tool_calls_log}
