"""
Azure SDK helpers — called by the admin agent for hub integration operations.
All credentials come from config.cfg (service principal).
"""
import logging
from config import cfg

log = logging.getLogger(__name__)


def _get_credential():
    from azure.identity import ClientSecretCredential
    return ClientSecretCredential(
        tenant_id=cfg.AZURE_TENANT_ID,
        client_id=cfg.AZURE_CLIENT_ID,
        client_secret=cfg.AZURE_CLIENT_SECRET,
    )


def _network_client(subscription_id: str):
    from azure.mgmt.network import NetworkManagementClient
    return NetworkManagementClient(_get_credential(), subscription_id)


# ── 1. Peer spoke VNET to hub ──────────────────────────────────────────────

def peer_hub_vnet(
    spoke_subscription_id: str,
    spoke_resource_group: str,
    spoke_vnet_name: str,
    spoke_address_space: str,
    allow_vnet_access: bool = None,
    allow_forwarded_traffic: bool = None,
    allow_gateway_transit: bool = None,
    use_remote_gateways: bool = None,
) -> dict:
    """
    Creates VNET peering in both directions (spoke→hub, hub→spoke).
    If peering settings are None, falls back to env var defaults.
    """
    try:
        # Use env var defaults for any unspecified settings
        allow_vnet_access       = cfg.PEERING_ALLOW_VNET_ACCESS      if allow_vnet_access       is None else allow_vnet_access
        allow_forwarded_traffic = cfg.PEERING_ALLOW_FORWARDED_TRAFFIC if allow_forwarded_traffic is None else allow_forwarded_traffic
        allow_gateway_transit   = cfg.PEERING_ALLOW_GATEWAY_TRANSIT   if allow_gateway_transit   is None else allow_gateway_transit
        use_remote_gateways     = cfg.PEERING_USE_REMOTE_GATEWAYS     if use_remote_gateways     is None else use_remote_gateways

        spoke_client = _network_client(spoke_subscription_id)
        hub_client   = _network_client(cfg.HUB_SUBSCRIPTION_ID)

        hub_vnet_id = (
            f"/subscriptions/{cfg.HUB_SUBSCRIPTION_ID}"
            f"/resourceGroups/{cfg.HUB_RESOURCE_GROUP}"
            f"/providers/Microsoft.Network/virtualNetworks/{cfg.HUB_VNET_NAME}"
        )
        spoke_vnet_id = (
            f"/subscriptions/{spoke_subscription_id}"
            f"/resourceGroups/{spoke_resource_group}"
            f"/providers/Microsoft.Network/virtualNetworks/{spoke_vnet_name}"
        )

        # Spoke → Hub
        log.info("Creating spoke→hub peering (%s → %s)", spoke_vnet_name, cfg.HUB_VNET_NAME)
        spoke_client.virtual_network_peerings.begin_create_or_update(
            resource_group_name=spoke_resource_group,
            virtual_network_name=spoke_vnet_name,
            virtual_network_peering_name="spoke-to-hub",
            virtual_network_peering_parameters={
                "allow_virtual_network_access": allow_vnet_access,
                "allow_forwarded_traffic":      allow_forwarded_traffic,
                "allow_gateway_transit":        False,  # spoke never grants transit
                "use_remote_gateways":          use_remote_gateways,
                "remote_virtual_network":       {"id": hub_vnet_id},
            },
        ).result()

        # Hub → Spoke
        log.info("Creating hub→spoke peering (%s → %s)", cfg.HUB_VNET_NAME, spoke_vnet_name)
        hub_client.virtual_network_peerings.begin_create_or_update(
            resource_group_name=cfg.HUB_RESOURCE_GROUP,
            virtual_network_name=cfg.HUB_VNET_NAME,
            virtual_network_peering_name=f"hub-to-{spoke_vnet_name}",
            virtual_network_peering_parameters={
                "allow_virtual_network_access": allow_vnet_access,
                "allow_forwarded_traffic":      allow_forwarded_traffic,
                "allow_gateway_transit":        allow_gateway_transit,
                "use_remote_gateways":          False,
                "remote_virtual_network":       {"id": spoke_vnet_id},
            },
        ).result()

        return {"success": True, "message": f"Peering created between {spoke_vnet_name} and {cfg.HUB_VNET_NAME}."}

    except Exception as exc:
        log.error("peer_hub_vnet failed: %s", exc)
        return {"success": False, "message": str(exc)}


def get_peering_defaults() -> dict:
    """Return current peering defaults from env vars."""
    return {
        "allow_vnet_access":       cfg.PEERING_ALLOW_VNET_ACCESS,
        "allow_forwarded_traffic": cfg.PEERING_ALLOW_FORWARDED_TRAFFIC,
        "allow_gateway_transit":   cfg.PEERING_ALLOW_GATEWAY_TRANSIT,
        "use_remote_gateways":     cfg.PEERING_USE_REMOTE_GATEWAYS,
    }


# ── 2. UDR — create route table ────────────────────────────────────────────

def create_route_table(
    name: str,
    resource_group: str,
    location: str = None,
    subscription_id: str = None,
    disable_bgp_route_propagation: bool = True,
) -> dict:
    """Create a new route table (UDR) in the given subscription/RG."""
    try:
        sub = subscription_id or cfg.SPOKE_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID
        loc = location or cfg.DEFAULT_AZURE_REGION
        client = _network_client(sub)
        log.info("Creating route table '%s' in %s/%s", name, resource_group, loc)
        result = client.route_tables.begin_create_or_update(
            resource_group_name=resource_group,
            route_table_name=name,
            parameters={
                "location": loc,
                "properties": {"disableBgpRoutePropagation": disable_bgp_route_propagation},
            },
        ).result()
        return {
            "success": True,
            "id":      result.id,
            "name":    result.name,
            "message": f"Route table '{name}' created in {resource_group}.",
        }
    except Exception as exc:
        log.error("create_route_table failed: %s", exc)
        return {"success": False, "message": str(exc)}


def add_route_to_table(
    route_table_name: str,
    resource_group: str,
    route_name: str,
    address_prefix: str,
    next_hop_type: str,
    next_hop_ip: str = None,
    subscription_id: str = None,
) -> dict:
    """Add a single route to a specific route table."""
    try:
        sub = subscription_id or cfg.SPOKE_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID
        client = _network_client(sub)
        params = {"address_prefix": address_prefix, "next_hop_type": next_hop_type}
        if next_hop_ip and next_hop_type == "VirtualAppliance":
            params["next_hop_ip_address"] = next_hop_ip

        log.info("Adding route '%s' to table '%s'", route_name, route_table_name)
        client.routes.begin_create_or_update(
            resource_group_name=resource_group,
            route_table_name=route_table_name,
            route_name=route_name,
            route_parameters=params,
        ).result()
        return {"success": True, "message": f"Route '{route_name}' ({address_prefix} → {next_hop_type}) added to {route_table_name}."}
    except Exception as exc:
        log.error("add_route_to_table failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── 3. UDR — list routes in a table ───────────────────────────────────────

def check_udr(
    udr_resource_group: str,
    udr_name: str,
    required_address_prefix: str,
) -> dict:
    try:
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        rt = client.route_tables.get(udr_resource_group, udr_name)
        routes = [
            {"name": r.name, "prefix": r.address_prefix, "next_hop": r.next_hop_ip_address}
            for r in (rt.routes or [])
        ]
        found = any(r["prefix"] == required_address_prefix for r in routes)
        return {
            "success": True,
            "found":   found,
            "routes":  routes,
            "message": (
                f"Route for {required_address_prefix} EXISTS in {udr_name}."
                if found else
                f"Route for {required_address_prefix} NOT FOUND in {udr_name}."
            ),
        }
    except Exception as exc:
        log.error("check_udr failed: %s", exc)
        return {"success": False, "found": False, "routes": [], "message": str(exc)}


def add_udr_routes(
    route_name: str,
    address_prefix: str,
    next_hop_type: str,
    next_hop_ip: str = None,
) -> dict:
    """Add a route to BOTH hub UDR_NAME_1 and UDR_NAME_2."""
    results = []
    try:
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        for udr_name in [cfg.UDR_NAME_1, cfg.UDR_NAME_2]:
            if not udr_name:
                continue
            try:
                params = {"address_prefix": address_prefix, "next_hop_type": next_hop_type}
                if next_hop_ip and next_hop_type == "VirtualAppliance":
                    params["next_hop_ip_address"] = next_hop_ip
                client.routes.begin_create_or_update(
                    resource_group_name=cfg.UDR_RESOURCE_GROUP,
                    route_table_name=udr_name,
                    route_name=route_name,
                    route_parameters=params,
                ).result()
                results.append({"udr": udr_name, "success": True, "message": f"Route added to {udr_name}."})
            except Exception as exc:
                results.append({"udr": udr_name, "success": False, "message": str(exc)})

        overall = all(r["success"] for r in results)
        return {"success": overall, "results": results,
                "message": "Routes added to both hub UDRs." if overall else "Some UDR updates failed."}
    except Exception as exc:
        log.error("add_udr_routes error: %s", exc)
        return {"success": False, "results": results, "message": str(exc)}


# ── 4. List VNET subnets ───────────────────────────────────────────────────

def list_vnet_subnets(
    subscription_id: str,
    resource_group: str,
    vnet_name: str,
) -> dict:
    """List all subnets in a spoke VNET."""
    try:
        client = _network_client(subscription_id)
        subnets = client.subnets.list(resource_group, vnet_name)
        result = []
        for s in subnets:
            rt_id = s.route_table.id if s.route_table else None
            result.append({
                "name":           s.name,
                "address_prefix": s.address_prefix,
                "route_table_id": rt_id,
                "has_udr":        rt_id is not None,
            })
        return {"success": True, "subnets": result, "count": len(result)}
    except Exception as exc:
        log.error("list_vnet_subnets failed: %s", exc)
        return {"success": False, "subnets": [], "message": str(exc)}


# ── 5. Assign UDR to a subnet ─────────────────────────────────────────────

def assign_route_table_to_subnet(
    subscription_id: str,
    resource_group: str,
    vnet_name: str,
    subnet_name: str,
    route_table_id: str,
) -> dict:
    """Associate a route table (UDR) with a specific subnet."""
    try:
        client = _network_client(subscription_id)
        subnet = client.subnets.get(resource_group, vnet_name, subnet_name)
        subnet.route_table = {"id": route_table_id}
        log.info("Assigning UDR %s to subnet %s/%s", route_table_id, vnet_name, subnet_name)
        client.subnets.begin_create_or_update(
            resource_group, vnet_name, subnet_name, subnet
        ).result()
        return {"success": True, "message": f"UDR assigned to subnet '{subnet_name}'."}
    except Exception as exc:
        log.error("assign_route_table_to_subnet failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── 6. Firewall — network rule ────────────────────────────────────────────

def add_firewall_network_rule(
    rule_name: str,
    destination_addresses: list,
    destination_ports: list,
    protocol: str = "TCP",
    source_addresses: list = None,
) -> dict:
    """Add a network rule to the configured firewall policy rule collection group."""
    try:
        from azure.mgmt.network.models import NetworkRule
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        rcg = client.firewall_policy_rule_collection_groups.get(
            cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME, cfg.FIREWALL_RULE_COLLECTION_GROUP
        )
        target = next(
            (rc for rc in (rcg.rule_collections or [])
             if rc.rule_collection_type == "FirewallPolicyFilterRuleCollection"), None
        )
        if target is None:
            return {"success": False, "message": "No FirewallPolicyFilterRuleCollection found."}

        new_rule = NetworkRule(
            name=rule_name, rule_type="NetworkRule",
            ip_protocols=[protocol],
            source_addresses=source_addresses or ["*"],
            destination_addresses=destination_addresses,
            destination_ports=destination_ports,
        )
        target.rules = list(target.rules or []) + [new_rule]
        log.info("Adding network rule '%s'", rule_name)
        client.firewall_policy_rule_collection_groups.begin_create_or_update(
            cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME,
            cfg.FIREWALL_RULE_COLLECTION_GROUP, rcg,
        ).result()
        return {"success": True, "message": f"Network rule '{rule_name}' added."}
    except Exception as exc:
        log.error("add_firewall_network_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── 7. Firewall — application rule (HTTP/HTTPS only) ─────────────────────

def add_firewall_application_rule(
    rule_name: str,
    target_fqdns: list,
    protocols: list,           # list of {"protocol_type": "Https"|"Http", "port": 443}
    source_addresses: list = None,
) -> dict:
    """
    Add an application rule (HTTP/HTTPS only) to the firewall policy.
    protocols must only contain Http or Https — other protocols are rejected.
    """
    # Validate protocols
    for p in protocols:
        pt = p.get("protocol_type", "")
        if pt not in ("Http", "Https"):
            return {
                "success": False,
                "message": f"Application rules only support Http/Https. '{pt}' is not allowed. Use a Network Rule for other protocols.",
            }

    try:
        from azure.mgmt.network.models import ApplicationRule, FirewallPolicyRuleApplicationProtocol
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        rcg = client.firewall_policy_rule_collection_groups.get(
            cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME, cfg.FIREWALL_RULE_COLLECTION_GROUP
        )
        target = next(
            (rc for rc in (rcg.rule_collections or [])
             if rc.rule_collection_type == "FirewallPolicyFilterRuleCollection"), None
        )
        if target is None:
            return {"success": False, "message": "No FirewallPolicyFilterRuleCollection found."}

        app_protocols = [
            FirewallPolicyRuleApplicationProtocol(
                protocol_type=p["protocol_type"],
                port=p.get("port", 443 if p["protocol_type"] == "Https" else 80),
            )
            for p in protocols
        ]
        new_rule = ApplicationRule(
            name=rule_name, rule_type="ApplicationRule",
            source_addresses=source_addresses or ["*"],
            target_fqdns=target_fqdns,
            protocols=app_protocols,
        )
        target.rules = list(target.rules or []) + [new_rule]
        log.info("Adding application rule '%s'", rule_name)
        client.firewall_policy_rule_collection_groups.begin_create_or_update(
            cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME,
            cfg.FIREWALL_RULE_COLLECTION_GROUP, rcg,
        ).result()
        return {"success": True, "message": f"Application rule '{rule_name}' added for {target_fqdns}."}
    except Exception as exc:
        log.error("add_firewall_application_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}
