"""
Automation agent — supports Anthropic Claude and OpenAI-compatible APIs
(Azure OpenAI, LM Studio, Ollama, any /v1/chat/completions endpoint).

Switch provider via AGENT_PROVIDER env var:
  AGENT_PROVIDER=anthropic   → uses ANTHROPIC_API_KEY + claude-sonnet-4-6
  AGENT_PROVIDER=openai      → uses OPENAI_API_KEY + OPENAI_BASE_URL + OPENAI_MODEL
"""
import json
import logging
import requests as http_requests

from config import cfg
import azure_tools
import notifications

log = logging.getLogger(__name__)

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
    hub_vnet=cfg.HUB_VNET_NAME                   or "<HUB_VNET_NAME not set>",
    hub_rg=cfg.HUB_RESOURCE_GROUP                or "<HUB_RESOURCE_GROUP not set>",
    hub_sub=cfg.HUB_SUBSCRIPTION_ID              or "<HUB_SUBSCRIPTION_ID not set>",
    udr1=cfg.UDR_NAME_1                          or "<UDR_NAME_1 not set>",
    udr2=cfg.UDR_NAME_2                          or "<UDR_NAME_2 not set>",
    udr_rg=cfg.UDR_RESOURCE_GROUP                or "<UDR_RESOURCE_GROUP not set>",
    fw_policy=cfg.FIREWALL_POLICY_NAME           or "<FIREWALL_POLICY_NAME not set>",
    fw_rcg=cfg.FIREWALL_RULE_COLLECTION_GROUP    or "<FIREWALL_RULE_COLLECTION_GROUP not set>",
)


# ── Tool definitions ───────────────────────────────────────────────────────
# Stored in OpenAI format (function calling).
# Anthropic format is derived from these at runtime.
TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "find_subnets",
            "description": "Find available subnets in the Presight subnet finder for a given pool and prefix length.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pool":   {"type": "string",  "description": "Pool key: '10.110' or '10.119'"},
                    "prefix": {"type": "integer", "description": "CIDR prefix length, e.g. 24 for /24"},
                },
                "required": ["pool", "prefix"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "allocate_subnet",
            "description": "Allocate a specific subnet and update the spoke request record.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pool":         {"type": "string"},
                    "subnet":       {"type": "string",  "description": "CIDR to allocate, e.g. '10.110.5.0/24'"},
                    "purpose":      {"type": "string"},
                    "requested_by": {"type": "string"},
                    "allocated_by": {"type": "string",  "description": "Usually 'Automation Agent'"},
                    "request_id":   {"type": "integer", "description": "Spoke request DB ID (optional)"},
                },
                "required": ["pool", "subnet", "purpose", "requested_by", "allocated_by"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_request",
            "description": "Fetch a spoke request and its VNET info from the database by ID.",
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
            "name": "peer_hub_vnet",
            "description": "Create VNET peering between a spoke VNET and the hub VNET in both directions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spoke_subscription_id": {"type": "string"},
                    "spoke_resource_group":  {"type": "string"},
                    "spoke_vnet_name":       {"type": "string"},
                    "spoke_address_space":   {"type": "string", "description": "CIDR of the spoke VNET"},
                },
                "required": ["spoke_subscription_id", "spoke_resource_group", "spoke_vnet_name", "spoke_address_space"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_udr",
            "description": "Check whether a required route prefix exists in a UDR table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "udr_name":                {"type": "string"},
                    "udr_resource_group":      {"type": "string", "description": "Defaults to UDR_RESOURCE_GROUP env var"},
                    "required_address_prefix": {"type": "string", "description": "CIDR to check for"},
                },
                "required": ["udr_name", "required_address_prefix"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_firewall_rule",
            "description": "Add an outbound network rule to the Azure Firewall policy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_name":             {"type": "string"},
                    "destination_addresses": {"type": "array", "items": {"type": "string"}},
                    "destination_ports":     {"type": "array", "items": {"type": "string"}},
                    "protocol":              {"type": "string", "default": "TCP"},
                    "source_addresses":      {"type": "array", "items": {"type": "string"}},
                },
                "required": ["rule_name", "destination_addresses", "destination_ports"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_udr_routes",
            "description": "Add a route to both configured UDR tables simultaneously.",
            "parameters": {
                "type": "object",
                "properties": {
                    "route_name":     {"type": "string"},
                    "address_prefix": {"type": "string"},
                    "next_hop_type":  {"type": "string", "description": "VirtualAppliance | VnetLocal | Internet | None"},
                    "next_hop_ip":    {"type": "string", "description": "Required if next_hop_type is VirtualAppliance"},
                },
                "required": ["route_name", "address_prefix", "next_hop_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_notification",
            "description": "Send a custom Teams notification message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":   {"type": "string"},
                    "message": {"type": "string"},
                    "level":   {"type": "string", "description": "info | success | warning | danger"},
                },
                "required": ["title", "message"],
            },
        },
    },
]

# Anthropic format derived from OpenAI definitions
TOOLS_ANTHROPIC = [
    {
        "name":         t["function"]["name"],
        "description":  t["function"]["description"],
        "input_schema": t["function"]["parameters"],
    }
    for t in TOOLS_OPENAI
]


# ── Client factory ─────────────────────────────────────────────────────────

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
            # Azure OpenAI
            _client = AzureOpenAI(
                azure_endpoint=cfg.OPENAI_BASE_URL,
                api_key=cfg.OPENAI_API_KEY,
                api_version=cfg.OPENAI_API_VERSION,
            )
        else:
            # Generic OpenAI-compatible (OpenAI, LM Studio, Ollama, etc.)
            kwargs = {"api_key": cfg.OPENAI_API_KEY or "not-needed"}
            if cfg.OPENAI_BASE_URL:
                kwargs["base_url"] = cfg.OPENAI_BASE_URL
            _client = OpenAI(**kwargs)

    else:
        raise RuntimeError(f"Unknown AGENT_PROVIDER '{provider}'. Use 'anthropic' or 'openai'.")

    return _client


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
            return json.dumps(azure_tools.peer_hub_vnet(**inputs))
        elif name == "check_udr":
            rg = inputs.pop("udr_resource_group", cfg.UDR_RESOURCE_GROUP)
            return json.dumps(azure_tools.check_udr(udr_resource_group=rg, **inputs))
        elif name == "add_firewall_rule":
            return json.dumps(azure_tools.add_firewall_rule(**inputs))
        elif name == "add_udr_routes":
            return json.dumps(azure_tools.add_udr_routes(**inputs))
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
            auth=auth, timeout=15, verify=False,
        )
        return json.dumps(resp.json())
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_allocate_subnet(pool, subnet, purpose, requested_by, allocated_by, request_id=None) -> str:
    auth = (cfg.SUBNET_FINDER_USER, cfg.SUBNET_FINDER_PASS) if cfg.SUBNET_FINDER_USER else None
    try:
        resp = http_requests.post(
            f"{cfg.SUBNET_FINDER_BASE_URL}/allocate",
            params={"pool": pool},
            data={"pool": pool, "selected": subnet, "purpose": purpose,
                  "requested_by": requested_by, "allocated_by": allocated_by},
            auth=auth, timeout=15, verify=False,
        )
        data = resp.json()
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
    Run the agentic loop. Works with both Anthropic and OpenAI providers.
    messages: list of {"role": "user"/"assistant", "content": str}
    Returns: {"reply": str, "tool_calls": [...]}
    """
    provider = cfg.AGENT_PROVIDER.lower()
    if provider == "anthropic":
        return _chat_anthropic(messages, max_iterations)
    else:
        return _chat_openai(messages, max_iterations)


def _chat_anthropic(messages: list, max_iterations: int) -> dict:
    import anthropic
    client = _get_client()
    tool_calls_log = []
    current_messages = list(messages)

    for _ in range(max_iterations):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS_ANTHROPIC,
            messages=current_messages,
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
                    tool_calls_log.append({"tool": block.name, "input": block.input, "result": result_str})
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result_str})

            current_messages.append({"role": "assistant", "content": assistant_content})
            current_messages.append({"role": "user",      "content": tool_results})
            continue

        break

    return {"reply": "Agent reached maximum iterations without a final answer.", "tool_calls": tool_calls_log}


def _chat_openai(messages: list, max_iterations: int) -> dict:
    client = _get_client()
    tool_calls_log = []
    # Convert simple string content to OpenAI message format
    current_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(messages)

    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=cfg.OPENAI_MODEL,
            tools=TOOLS_OPENAI,

            messages=current_messages,
        )

        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        if finish_reason == "stop" or not msg.tool_calls:
            return {"reply": msg.content or "", "tool_calls": tool_calls_log}

        if finish_reason == "tool_calls":
            # Append assistant message with tool_calls
            current_messages.append(msg)

            for tc in msg.tool_calls:
                try:
                    inputs = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    inputs = {}
                result_str = _execute_tool(tc.function.name, inputs)
                tool_calls_log.append({"tool": tc.function.name, "input": inputs, "result": result_str})
                # Feed result back
                current_messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      result_str,
                })
            continue

        break

    return {"reply": "Agent reached maximum iterations without a final answer.", "tool_calls": tool_calls_log}
