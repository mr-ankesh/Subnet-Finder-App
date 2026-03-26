"""
Azure SDK helpers — called by the agent for Step 4 operations.
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
) -> dict:
    """
    Creates VNET peering in both directions:
      spoke  → hub  (peering name: spoke-to-hub)
      hub    → spoke (peering name: hub-to-<spoke_vnet_name>)
    Returns {"success": bool, "message": str}
    """
    try:
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
                "allow_virtual_network_access": True,
                "allow_forwarded_traffic":      True,
                "allow_gateway_transit":        False,
                "use_remote_gateways":          False,
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
                "allow_virtual_network_access": True,
                "allow_forwarded_traffic":      True,
                "allow_gateway_transit":        True,
                "use_remote_gateways":          False,
                "remote_virtual_network":       {"id": spoke_vnet_id},
            },
        ).result()

        return {"success": True, "message": f"Peering created between {spoke_vnet_name} and {cfg.HUB_VNET_NAME}."}

    except Exception as exc:
        log.error("peer_hub_vnet failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── 2. Check UDR ──────────────────────────────────────────────────────────

def check_udr(
    udr_resource_group: str,
    udr_name: str,
    required_address_prefix: str,
) -> dict:
    """
    Checks whether a route for required_address_prefix exists in the given UDR.
    Returns {"success": bool, "found": bool, "routes": [...], "message": str}
    """
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


# ── 3. Add firewall rule ──────────────────────────────────────────────────

def add_firewall_rule(
    rule_name: str,
    destination_addresses: list,
    destination_ports: list,
    protocol: str = "TCP",
    source_addresses: list = None,
) -> dict:
    """
    Adds a network rule to the configured Azure Firewall policy rule collection group.
    Returns {"success": bool, "message": str}
    """
    try:
        from azure.mgmt.network.models import (
            FirewallPolicyRuleCollectionGroup,
            FirewallPolicyFilterRuleCollection,
            NetworkRule,
        )
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)

        # Get existing rule collection group
        rcg = client.firewall_policy_rule_collection_groups.get(
            resource_group_name=cfg.FIREWALL_POLICY_RG,
            firewall_policy_name=cfg.FIREWALL_POLICY_NAME,
            rule_collection_group_name=cfg.FIREWALL_RULE_COLLECTION_GROUP,
        )

        # Find the target rule collection (first FilterRuleCollection found)
        target_collection = None
        for rc in (rcg.rule_collections or []):
            if rc.rule_collection_type == "FirewallPolicyFilterRuleCollection":
                target_collection = rc
                break

        if target_collection is None:
            return {"success": False, "message": "No FirewallPolicyFilterRuleCollection found in rule collection group."}

        new_rule = NetworkRule(
            name=rule_name,
            rule_type="NetworkRule",
            ip_protocols=[protocol],
            source_addresses=source_addresses or ["*"],
            destination_addresses=destination_addresses,
            destination_ports=destination_ports,
        )
        target_collection.rules = list(target_collection.rules or []) + [new_rule]

        log.info("Adding firewall rule '%s' to policy '%s'", rule_name, cfg.FIREWALL_POLICY_NAME)
        client.firewall_policy_rule_collection_groups.begin_create_or_update(
            resource_group_name=cfg.FIREWALL_POLICY_RG,
            firewall_policy_name=cfg.FIREWALL_POLICY_NAME,
            rule_collection_group_name=cfg.FIREWALL_RULE_COLLECTION_GROUP,
            parameters=rcg,
        ).result()

        return {"success": True, "message": f"Firewall rule '{rule_name}' added for {destination_addresses} on ports {destination_ports}."}

    except Exception as exc:
        log.error("add_firewall_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── 4. Add routes to both UDRs ────────────────────────────────────────────

def add_udr_routes(
    route_name: str,
    address_prefix: str,
    next_hop_type: str,
    next_hop_ip: str = None,
) -> dict:
    """
    Adds a route to BOTH UDR_NAME_1 and UDR_NAME_2.
    next_hop_type: "VirtualAppliance" | "VnetLocal" | "Internet" | "None"
    Returns {"success": bool, "results": [{"udr": ..., "success": ..., "message": ...}]}
    """
    results = []
    try:
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)

        for udr_name in [cfg.UDR_NAME_1, cfg.UDR_NAME_2]:
            if not udr_name:
                continue
            try:
                params = {
                    "address_prefix":  address_prefix,
                    "next_hop_type":   next_hop_type,
                }
                if next_hop_ip and next_hop_type == "VirtualAppliance":
                    params["next_hop_ip_address"] = next_hop_ip

                log.info("Adding route '%s' to UDR '%s'", route_name, udr_name)
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
        return {
            "success": overall,
            "results": results,
            "message": "Routes added to both UDRs." if overall else "Some UDR updates failed — check results.",
        }

    except Exception as exc:
        log.error("add_udr_routes outer error: %s", exc)
        return {"success": False, "results": results, "message": str(exc)}
