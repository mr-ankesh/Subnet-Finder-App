"""
Admin Agent — handles CIDR assignment and Azure hub integration operations.
Admin-only. Requesters use agent_requester.py.
"""
import json
import logging
from datetime import datetime

from config import cfg
import azure_tools
import notifications

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Presight R&D Azure Network Admin Agent.

You help the network admin team manage spoke VNET requests end-to-end.

YOUR CAPABILITIES:
1. LIST / VIEW requests — show all requests or details of a specific one.
2. ASSIGN CIDR — find available subnets and assign to a request.
3. DEALLOCATE CIDR — release an assigned subnet back to the pool (requires reason).
4. UPDATE STATUS — change request status (Hub Integration In Progress, Hub Integrated).
5. PEER with Hub — create VNET peering with default settings or custom.
6. CREATE UDR — create a new route table, add routes, list spoke subnets, assign UDR.
7. FIREWALL RULES — add Application Rule (HTTP/HTTPS only) or Network Rule.
8. SEND NOTIFICATIONS — send custom Teams messages.

CIDR ASSIGNMENT WORKFLOW (STRICTLY FOLLOW THIS):
- Step 1: Call find_available_subnets to get the list of available CIDRs.
- Step 2: Present the available options to the admin clearly.
- Step 3: WAIT for the admin to explicitly select and confirm a specific subnet.
- Step 4: Only after explicit confirmation, call assign_cidr_to_request.
- NEVER auto-select or auto-assign a CIDR. Always require admin to choose.

CIDR DEALLOCATION:
- Always ask for a reason before deallocating.
- Status will revert to CIDR_REQUESTED so the request can be re-assigned.

WORKFLOW:
- Step 2: Admin assigns CIDR → use assign_cidr_to_request → status becomes CIDR_ASSIGNED
- Step 4a: Change status to HUB_INTEGRATION_IN_PROGRESS → notifies requester
- Step 4b: Run hub integration tasks (peer, UDR, firewall) → change to HUB_INTEGRATED

PEERING GUIDANCE:
- Always ask: "Use default peering settings or specify custom?"
- Default settings come from env vars — show them to admin before applying.
- Custom: ask for each setting individually.

UDR GUIDANCE:
- First create the route table, add required routes (always add the spoke CIDR route to hub firewall as next hop).
- Then list spoke subnets and ask admin which subnet(s) to assign the UDR to.

FIREWALL RULES:
- Ask: Application Rule or Network Rule?
- Application Rule: only HTTP/HTTPS destinations — validate this strictly.
- Network Rule: any protocol, IP destinations.
- Always confirm the rule collection group (show default from env).

Azure environment:
- Hub VNET: {hub_vnet} (RG: {hub_rg}, Sub: {hub_sub})
- Hub UDR 1: {udr1} | Hub UDR 2: {udr2} (RG: {udr_rg})
- Firewall Policy: {fw_policy} / RCG: {fw_rcg}
- Default region: {region}
""".format(
    hub_vnet=cfg.HUB_VNET_NAME              or "<not set>",
    hub_rg=cfg.HUB_RESOURCE_GROUP           or "<not set>",
    hub_sub=cfg.HUB_SUBSCRIPTION_ID         or "<not set>",
    udr1=cfg.UDR_NAME_1                     or "<not set>",
    udr2=cfg.UDR_NAME_2                     or "<not set>",
    udr_rg=cfg.UDR_RESOURCE_GROUP           or "<not set>",
    fw_policy=cfg.FIREWALL_POLICY_NAME      or "<not set>",
    fw_rcg=cfg.FIREWALL_RULE_COLLECTION_GROUP or "<not set>",
    region=cfg.DEFAULT_AZURE_REGION,
)

# ── Tool definitions ───────────────────────────────────────────────────────

TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "list_requests",
            "description": "List all spoke requests, optionally filtered by status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status_filter": {"type": "string", "description": "Filter by status (optional). E.g. CIDR_REQUESTED"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_request_details",
            "description": "Get full details of a specific spoke request including VNET info.",
            "parameters": {
                "type": "object",
                "properties": {"request_id": {"type": "integer"}},
                "required": ["request_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_available_subnets",
            "description": "Find available subnets in a pool for a given CIDR prefix. ALWAYS call this first and present results to admin before assigning. Never auto-assign.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pool":   {"type": "string", "description": "Pool key: '10.110' or '10.119'"},
                    "prefix": {"type": "integer", "description": "CIDR prefix length, e.g. 24"},
                },
                "required": ["pool", "prefix"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_cidr_to_request",
            "description": "Allocate a specific subnet (chosen and confirmed by admin) and assign it to a spoke request. ONLY call this after admin has explicitly selected a subnet from the list. Updates status to CIDR_ASSIGNED.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id":   {"type": "integer"},
                    "pool":         {"type": "string", "description": "Pool key: '10.110' or '10.119'"},
                    "subnet":       {"type": "string", "description": "CIDR to allocate, e.g. '10.110.5.0/24'"},
                    "allocated_by": {"type": "string", "description": "Admin name or 'Admin Agent'"},
                },
                "required": ["request_id", "pool", "subnet", "allocated_by"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deallocate_cidr_from_request",
            "description": "Release an assigned subnet back to the pool and revert request status to CIDR_REQUESTED. Always collect reason before calling.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "integer"},
                    "reason":     {"type": "string", "description": "Reason for deallocating the CIDR (required)"},
                },
                "required": ["request_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_request_status",
            "description": "Update a request status. Admin can set: HUB_INTEGRATION_IN_PROGRESS or HUB_INTEGRATED.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "integer"},
                    "status":     {"type": "string", "description": "HUB_INTEGRATION_IN_PROGRESS or HUB_INTEGRATED"},
                    "notes":      {"type": "string", "description": "Optional notes"},
                },
                "required": ["request_id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_peering_defaults",
            "description": "Get the current default VNET peering settings from environment configuration.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "peer_hub_vnet",
            "description": "Create VNET peering between a spoke VNET and the hub in both directions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spoke_subscription_id":    {"type": "string"},
                    "spoke_resource_group":     {"type": "string"},
                    "spoke_vnet_name":          {"type": "string"},
                    "spoke_address_space":      {"type": "string"},
                    "allow_vnet_access":        {"type": "boolean", "description": "Leave null to use env default"},
                    "allow_forwarded_traffic":  {"type": "boolean"},
                    "allow_gateway_transit":    {"type": "boolean"},
                    "use_remote_gateways":      {"type": "boolean"},
                },
                "required": ["spoke_subscription_id", "spoke_resource_group", "spoke_vnet_name", "spoke_address_space"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_route_table",
            "description": "Create a new UDR route table in Azure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name":            {"type": "string"},
                    "resource_group":  {"type": "string"},
                    "location":        {"type": "string", "description": "Azure region. Uses DEFAULT_AZURE_REGION if omitted."},
                    "subscription_id": {"type": "string", "description": "Uses spoke sub if omitted."},
                    "disable_bgp_route_propagation": {"type": "boolean", "default": True},
                },
                "required": ["name", "resource_group"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_route_to_udr",
            "description": "Add a single route to a specific route table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "route_table_name": {"type": "string"},
                    "resource_group":   {"type": "string"},
                    "route_name":       {"type": "string"},
                    "address_prefix":   {"type": "string"},
                    "next_hop_type":    {"type": "string", "description": "VirtualAppliance | VnetLocal | Internet | None"},
                    "next_hop_ip":      {"type": "string"},
                    "subscription_id":  {"type": "string"},
                },
                "required": ["route_table_name", "resource_group", "route_name", "address_prefix", "next_hop_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_spoke_subnets",
            "description": "List all subnets in a spoke VNET to determine where to assign a UDR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subscription_id": {"type": "string"},
                    "resource_group":  {"type": "string"},
                    "vnet_name":       {"type": "string"},
                },
                "required": ["subscription_id", "resource_group", "vnet_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_udr_to_subnet",
            "description": "Associate a route table (UDR) with a specific subnet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subscription_id":  {"type": "string"},
                    "resource_group":   {"type": "string"},
                    "vnet_name":        {"type": "string"},
                    "subnet_name":      {"type": "string"},
                    "route_table_id":   {"type": "string", "description": "Full ARM resource ID of the route table"},
                },
                "required": ["subscription_id", "resource_group", "vnet_name", "subnet_name", "route_table_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_firewall_network_rule",
            "description": "Add a network rule (any protocol) to the Azure Firewall policy.",
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
            "name": "add_firewall_application_rule",
            "description": "Add an application rule (HTTP/HTTPS ONLY) to the Azure Firewall policy. Rejects other protocols.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_name":       {"type": "string"},
                    "target_fqdns":    {"type": "array", "items": {"type": "string"}, "description": "FQDNs to allow"},
                    "protocols": {
                        "type": "array",
                        "description": "List of protocols — only Http/Https allowed",
                        "items": {
                            "type": "object",
                            "properties": {
                                "protocol_type": {"type": "string", "enum": ["Http", "Https"]},
                                "port":          {"type": "integer"},
                            },
                        },
                    },
                    "source_addresses": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["rule_name", "target_fqdns", "protocols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_notification",
            "description": "Send a custom Teams notification.",
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
        if name == "list_requests":
            return _tool_list_requests(**inputs)
        elif name == "get_request_details":
            return _tool_get_request(**inputs)
        elif name == "find_available_subnets":
            return _tool_find_subnets(**inputs)
        elif name == "assign_cidr_to_request":
            return _tool_assign_cidr(**inputs)
        elif name == "deallocate_cidr_from_request":
            return _tool_deallocate_cidr(**inputs)
        elif name == "update_request_status":
            return _tool_update_status(**inputs)
        elif name == "get_peering_defaults":
            return json.dumps(azure_tools.get_peering_defaults())
        elif name == "peer_hub_vnet":
            return json.dumps(azure_tools.peer_hub_vnet(**inputs))
        elif name == "create_route_table":
            return json.dumps(azure_tools.create_route_table(**inputs))
        elif name == "add_route_to_udr":
            return json.dumps(azure_tools.add_route_to_table(**inputs))
        elif name == "list_spoke_subnets":
            return json.dumps(azure_tools.list_vnet_subnets(**inputs))
        elif name == "assign_udr_to_subnet":
            return json.dumps(azure_tools.assign_route_table_to_subnet(**inputs))
        elif name == "add_firewall_network_rule":
            return json.dumps(azure_tools.add_firewall_network_rule(**inputs))
        elif name == "add_firewall_application_rule":
            return json.dumps(azure_tools.add_firewall_application_rule(**inputs))
        elif name == "send_notification":
            ok = notifications.notify_custom(
                title=inputs.get("title", "Admin Notification"),
                message=inputs.get("message", ""),
                level=inputs.get("level", "info"),
            )
            return json.dumps({"success": ok})
        else:
            return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as exc:
        log.error("Admin tool '%s' raised: %s", name, exc)
        return json.dumps({"error": str(exc)})


def _tool_list_requests(status_filter: str = None) -> str:
    try:
        from models import SpokeRequest
        from app import app
        with app.app_context():
            q = SpokeRequest.query.order_by(SpokeRequest.created_at.desc())
            if status_filter:
                q = q.filter(SpokeRequest.status == status_filter)
            reqs = q.all()
            return json.dumps([r.to_dict() for r in reqs])
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_get_request(request_id: int) -> str:
    try:
        from models import SpokeRequest
        from app import app
        with app.app_context():
            req = SpokeRequest.query.get(request_id)
            if not req:
                return json.dumps({"error": f"Request #{request_id} not found."})
            data = req.to_dict()
            if req.vnet_info:
                data["vnet_info"] = req.vnet_info.to_dict()
        return json.dumps(data)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


_POOLS = {"10.110": "10.110.0.0/16", "10.119": "10.119.0.0/16"}

def _tool_find_subnets(pool: str, prefix: int) -> str:
    import ipaddress as _ip
    if pool not in _POOLS:
        return json.dumps({"error": f"Invalid pool. Must be one of: {list(_POOLS.keys())}"})
    if not (8 <= prefix <= 29):
        return json.dumps({"error": "Prefix must be between /8 and /29"})
    try:
        from app import load_subnets, compute_free_blocks, candidates_from_free
        base_net = _ip.ip_network(_POOLS[pool])
        df = load_subnets()
        free_blocks = compute_free_blocks(base_net, df)
        candidates, truncated = candidates_from_free(free_blocks, prefix, limit=20)
        return json.dumps({
            "pool": pool,
            "prefix": f"/{prefix}",
            "candidates": candidates,
            "total_shown": len(candidates),
            "truncated": truncated,
            "message": (
                f"Found {len(candidates)} available /{prefix} subnets in {_POOLS[pool]}. "
                "Present these to admin and ask them to select one."
            ),
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_assign_cidr(request_id: int, pool: str, subnet: str, allocated_by: str) -> str:
    import ipaddress as _ip
    if pool not in _POOLS:
        return json.dumps({"error": f"Invalid pool '{pool}'. Must be one of: {list(_POOLS.keys())}"})
    try:
        from models import db, SpokeRequest, RequestStatus
        from app import app, allocate_subnet
        with app.app_context():
            req = SpokeRequest.query.get(request_id)
            if not req:
                return json.dumps({"error": f"Request #{request_id} not found."})
            if req.status != RequestStatus.CIDR_REQUESTED:
                return json.dumps({"error": f"Request is already in status '{req.status_label()}'. Cannot assign CIDR."})

            base_net = _ip.ip_network(_POOLS[pool])
            ok, msg = allocate_subnet(
                selected_cidr=subnet,
                base_net=base_net,
                purpose=req.purpose,
                requested_by=req.requester_name,
                allocated_by=allocated_by,
            )
            if not ok:
                return json.dumps({"error": msg})

            req.allocated_subnet = subnet
            req.status = RequestStatus.CIDR_ASSIGNED
            req.updated_at = datetime.utcnow()
            db.session.commit()
            notifications.notify_cidr_assigned(req, subnet)

        return json.dumps({"success": True, "request_id": request_id, "subnet": subnet,
                           "message": f"Subnet {subnet} assigned to request #{request_id}. Teams notification sent."})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_deallocate_cidr(request_id: int, reason: str) -> str:
    import ipaddress as _ip
    if not reason or not reason.strip():
        return json.dumps({"error": "Reason is required for deallocation."})
    try:
        from models import db, SpokeRequest, RequestStatus
        from app import app, deallocate_subnet
        with app.app_context():
            req = SpokeRequest.query.get(request_id)
            if not req:
                return json.dumps({"error": f"Request #{request_id} not found."})
            if not req.allocated_subnet:
                return json.dumps({"error": f"Request #{request_id} has no allocated subnet."})
            if req.status not in (RequestStatus.CIDR_ASSIGNED, RequestStatus.VNET_CREATED,
                                   RequestStatus.HUB_INTEGRATION_NEEDED, RequestStatus.HUB_INTEGRATION_IN_PROGRESS):
                return json.dumps({"error": f"Cannot deallocate — status is '{req.status_label()}'. Only pre-integration statuses are allowed."})

            subnet = req.allocated_subnet
            # Determine pool from subnet
            pool_key = None
            for key, cidr in _POOLS.items():
                try:
                    if _ip.ip_network(subnet).subnet_of(_ip.ip_network(cidr)):
                        pool_key = key
                        break
                except Exception:
                    continue

            if pool_key:
                import ipaddress
                base_net = ipaddress.ip_network(_POOLS[pool_key])
                ok, msg = deallocate_subnet(subnet, base_net)
                if not ok:
                    return json.dumps({"error": f"Failed to release subnet: {msg}"})

            # Record deallocation reason and revert status
            old_notes = req.notes or ""
            req.notes = f"{old_notes}\n[DEALLOCATED {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC] {reason}".strip()
            req.allocated_subnet = None
            req.status = RequestStatus.CIDR_REQUESTED
            req.updated_at = datetime.utcnow()
            db.session.commit()

            try:
                notifications.notify_custom(
                    title=f"CIDR Deallocated — Request #{request_id}",
                    message=f"Subnet **{subnet}** has been released back to the pool.\n\n**Reason:** {reason}\n\nRequest reverted to CIDR_REQUESTED status.",
                    level="warning",
                )
            except Exception:
                pass

        return json.dumps({
            "success": True,
            "message": f"Subnet {subnet} released from request #{request_id}. Status reverted to CIDR_REQUESTED. Reason recorded.",
            "subnet": subnet,
            "reason": reason,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_update_status(request_id: int, status: str, notes: str = None) -> str:
    from models import RequestStatus
    admin_statuses = [RequestStatus.HUB_INTEGRATION_IN_PROGRESS, RequestStatus.HUB_INTEGRATED]
    if status not in admin_statuses:
        return json.dumps({"error": f"Admin can only set: {admin_statuses}"})
    try:
        from models import db, SpokeRequest
        from app import app
        with app.app_context():
            req = SpokeRequest.query.get(request_id)
            if not req:
                return json.dumps({"error": f"Request #{request_id} not found."})
            req.status = status
            req.updated_at = datetime.utcnow()
            if notes:
                req.notes = notes
            db.session.commit()

            if status == RequestStatus.HUB_INTEGRATION_IN_PROGRESS:
                notifications.notify_hub_in_progress(req)
            elif status == RequestStatus.HUB_INTEGRATED:
                notifications.notify_hub_integrated(req)

        return json.dumps({"success": True, "message": f"Request #{request_id} status updated to {status}."})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ── Client + chat (same pattern as requester agent) ───────────────────────

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


def chat(messages: list, max_iterations: int = 10) -> dict:
    provider = cfg.AGENT_PROVIDER.lower()
    return _chat_anthropic(messages, max_iterations) if provider == "anthropic" else _chat_openai(messages, max_iterations)


def _chat_anthropic(messages, max_iterations):
    client = _get_client()
    tool_calls_log = []
    current_messages = list(messages)

    for _ in range(max_iterations):
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=4096,
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
    return {"reply": "Reached maximum steps.", "tool_calls": tool_calls_log}


def _chat_openai(messages, max_iterations):
    client = _get_client()
    tool_calls_log = []
    current_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(messages)

    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=cfg.OPENAI_MODEL, tools=TOOLS_OPENAI, tool_choice="auto", messages=current_messages,
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
    return {"reply": "Reached maximum steps.", "tool_calls": tool_calls_log}
