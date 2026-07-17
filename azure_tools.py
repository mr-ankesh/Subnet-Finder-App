"""
Azure SDK helpers — called by the admin agent for hub integration operations.
Credentials come from config.cfg: Service Principal or Managed Identity,
selected by the AZURE_AUTH_MODE setting (editable in /admin/settings).
"""
import functools
import logging
from config import cfg
from naming import render_name

log = logging.getLogger(__name__)


def _guard(fn):
    """When AZURE_DRY_RUN is on, simulate the call — never touch Azure."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if cfg.AZURE_DRY_RUN:
            detail = ", ".join(f"{k}={v}" for k, v in kwargs.items()
                               if v not in (None, "") and isinstance(v, (str, int, bool)))
            log.info("[dry-run] %s skipped (AZURE_DRY_RUN on) %s", fn.__name__, detail)
            return {"success": True, "dry_run": True,
                    "message": f"[dry-run] {fn.__name__} simulated — no Azure changes made."
                               + (f" ({detail})" if detail else "")}
        return fn(*args, **kwargs)
    return wrapper


def _get_credential():
    if cfg.AZURE_AUTH_MODE == "managed_identity":
        from azure.identity import ManagedIdentityCredential
        return ManagedIdentityCredential(client_id=cfg.AZURE_MI_CLIENT_ID or None)
    from azure.identity import ClientSecretCredential
    return ClientSecretCredential(
        tenant_id=cfg.AZURE_TENANT_ID,
        client_id=cfg.AZURE_CLIENT_ID,
        client_secret=cfg.AZURE_CLIENT_SECRET,
    )


def test_connection() -> dict:
    """
    Read-only connectivity check for the settings UI: authenticate with the
    configured credential and fetch the hub VNET. Never mutates anything,
    so it runs for real even when AZURE_DRY_RUN is on.
    """
    if not cfg.HUB_SUBSCRIPTION_ID or not cfg.HUB_RESOURCE_GROUP or not cfg.HUB_VNET_NAME:
        return {"success": False,
                "message": "Set Hub subscription ID, resource group and VNET name first."}
    try:
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        vnet = client.virtual_networks.get(cfg.HUB_RESOURCE_GROUP, cfg.HUB_VNET_NAME)
        spaces = ", ".join(vnet.address_space.address_prefixes or [])
        return {"success": True,
                "message": f"Connected ({cfg.AZURE_AUTH_MODE}). Hub VNET '{vnet.name}' found — address space: {spaces}."}
    except Exception as exc:
        log.error("test_connection failed: %s", exc)
        return {"success": False, "message": str(exc)}


def _network_client(subscription_id: str):
    from azure.mgmt.network import NetworkManagementClient
    return NetworkManagementClient(_get_credential(), subscription_id)


# ── 0. Create resource group + spoke VNET + subnet ─────────────────────────

def _resource_client(subscription_id: str):
    from azure.mgmt.resource.resources import ResourceManagementClient
    return ResourceManagementClient(_get_credential(), subscription_id)


@_guard
def ensure_resource_group(subscription_id: str, resource_group: str, location: str) -> dict:
    """Create the resource group if it doesn't already exist."""
    try:
        client = _resource_client(subscription_id)
        if client.resource_groups.check_existence(resource_group):
            return {"success": True, "created": False,
                    "message": f"Resource group '{resource_group}' already exists."}
        client.resource_groups.create_or_update(resource_group, {"location": location})
        return {"success": True, "created": True,
                "message": f"Resource group '{resource_group}' created in {location}."}
    except Exception as exc:
        log.error("ensure_resource_group failed: %s", exc)
        return {"success": False, "created": False, "message": str(exc)}


def _subnet_cidr(address_space: str, subnet_size) -> str:
    """First subnet block of the requested size within the VNET address space."""
    import ipaddress
    net = ipaddress.ip_network(address_space, strict=False)
    try:
        sz = int(str(subnet_size).lstrip("/")) if subnet_size else net.prefixlen
    except Exception:
        sz = net.prefixlen
    if sz < net.prefixlen:          # a subnet can't be larger than its VNET
        sz = net.prefixlen
    return str(next(net.subnets(new_prefix=sz)))


def carve_subnets(address_space: str, entries: list) -> list:
    """
    Sequentially carve N subnets inside the VNET address space.
    entries: [{"name": ..., "size": 26}, ...] → [{"name", "address_prefix"}, ...]
    Raises ValueError when they don't fit.
    """
    import ipaddress
    net = ipaddress.ip_network(address_space, strict=False)
    addr, out = int(net.network_address), []
    for i, e in enumerate(entries):
        try:
            sz = int(str(e.get("size", "")).lstrip("/"))
        except (TypeError, ValueError):
            sz = net.prefixlen
        if sz < net.prefixlen:
            raise ValueError(f"Subnet #{i + 1} (/{sz}) is larger than the VNET ({address_space}).")
        block_size = 2 ** (32 - sz)
        if addr % block_size:                      # align up to the block boundary
            addr = (addr // block_size + 1) * block_size
        block = ipaddress.ip_network((addr, sz))
        if not block.subnet_of(net):
            raise ValueError(f"Requested subnets do not fit inside {address_space} "
                             f"(ran out at subnet #{i + 1}, /{sz}).")
        out.append({"name": (e.get("name") or f"subnet{i + 1}").strip(),
                    "address_prefix": str(block)})
        addr += block_size
    return out


@_guard
def create_spoke_vnet(
    subscription_id: str,
    resource_group: str,
    vnet_name: str,
    location: str,
    address_space: str,
    subnet_name: str = "default",
    subnet_size=None,
    subnets: list = None,           # [{"name", "size"}, ...] — overrides the single-subnet args
    on_conflict: str = None,        # "replace" = overwrite an existing VNET after confirmation
) -> dict:
    """Ensure the RG exists, then create the spoke VNET with the requested subnet(s)."""
    try:
        # No silent overwrite: create_or_update REPLACES an existing VNET
        # (address space and subnets) — surface its current state first.
        before = None
        try:
            existing = _network_client(subscription_id).virtual_networks.get(
                resource_group, vnet_name)
            before = {"name": existing.name, "location": existing.location,
                      "address_space": list(existing.address_space.address_prefixes or []),
                      "subnets": [{"name": s.name,
                                   "prefix": s.address_prefix or ", ".join(
                                       getattr(s, "address_prefixes", None) or [])}
                                  for s in (existing.subnets or [])]}
            if on_conflict != "replace":
                return {"success": False, "conflict": True, "existing_vnet": before,
                        "message": f"VNET '{vnet_name}' already exists in {resource_group} "
                                   f"({', '.join(before['address_space'])}, "
                                   f"{len(before['subnets'])} subnet(s)) — deploying would "
                                   f"OVERWRITE its address space and subnets."}
        except Exception as exc:
            if not _is_not_found(exc):
                raise

        rg_res = ensure_resource_group(subscription_id, resource_group, location)
        if not rg_res.get("success"):
            return {"success": False, "message": f"Resource group: {rg_res.get('message')}"}

        if subnets:
            try:
                subnet_params = carve_subnets(address_space, subnets)
            except ValueError as exc:
                return {"success": False, "message": str(exc)}
        else:
            subnet_params = [{"name": subnet_name or "default",
                              "address_prefix": _subnet_cidr(address_space, subnet_size)}]
        client = _network_client(subscription_id)
        log.info("Creating VNET '%s' (%s) with %d subnet(s) in %s/%s",
                 vnet_name, address_space, len(subnet_params), resource_group, location)
        client.virtual_networks.begin_create_or_update(
            resource_group_name=resource_group,
            virtual_network_name=vnet_name,
            parameters={
                "location": location,
                "address_space": {"address_prefixes": [address_space]},
                "subnets": subnet_params,
            },
        ).result()
        snet_desc = ", ".join(f"{s['name']} ({s['address_prefix']})" for s in subnet_params)
        msg = f"VNET '{vnet_name}' ({address_space}) created with subnet(s): {snet_desc}."
        if rg_res.get("created"):
            msg = f"RG '{resource_group}' created. " + msg
        after = {"name": vnet_name, "location": location, "address_space": [address_space],
                 "subnets": [{"name": s["name"], "prefix": s["address_prefix"]}
                             for s in subnet_params]}
        change = {"target": f"VNET {vnet_name} @ {resource_group}",
                  "before": before, "after": after}
        if before:      # overwrote an existing VNET — revert restores its old definition
            change.update({"revert_op": "restore_vnet",
                           "revert_params": {"sub": subscription_id, "rg": resource_group,
                                             "vnet": vnet_name, "location": before["location"],
                                             "address_space": before["address_space"],
                                             "subnets": before["subnets"]}})
        else:
            change.update({"revert_op": "delete_vnet",
                           "revert_params": {"sub": subscription_id, "rg": resource_group,
                                             "vnet": vnet_name}})
        return {"success": True, "replaced_existing": bool(before), "change": change, "message": msg}
    except Exception as exc:
        log.error("create_spoke_vnet failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def delete_spoke_route_table(subscription_id: str, resource_group: str,
                             vnet_name: str, route_table_name: str) -> dict:
    """Disassociate a route table from the VNET's subnets, then delete it.
    Routes and associations are snapshotted so the deletion can be reverted."""
    try:
        client = _network_client(subscription_id)
        before = None
        try:
            rt = client.route_tables.get(resource_group, route_table_name)
            before = {"location": rt.location,
                      "routes": [{"name": r.name, "prefix": r.address_prefix,
                                  "next_hop_type": str(r.next_hop_type or ""),
                                  "next_hop_ip": r.next_hop_ip_address or ""}
                                 for r in (rt.routes or [])],
                      "assigned_subnets": []}
        except Exception as exc:
            if not _is_not_found(exc):
                raise
        cleared = []
        try:
            for s in client.subnets.list(resource_group, vnet_name):
                if s.route_table and s.route_table.id.split("/")[-1] == route_table_name:
                    s.route_table = None
                    client.subnets.begin_create_or_update(
                        resource_group, vnet_name, s.name, s).result()
                    cleared.append(s.name)
        except Exception as exc:
            if not _is_not_found(exc):        # VNET already gone → nothing associated
                raise
        if before is not None:
            before["assigned_subnets"] = cleared
        client.route_tables.begin_delete(resource_group, route_table_name).result()
        msg = f"Route table '{route_table_name}' deleted."
        if cleared:
            msg = f"Unassigned from subnet(s) {', '.join(cleared)}. " + msg
        res = {"success": True, "message": msg}
        if before is not None:
            res["change"] = {"target": f"route table {route_table_name} @ {resource_group}",
                             "before": before, "after": None,
                             "revert_op": "restore_spoke_rt",
                             "revert_params": {"sub": subscription_id, "rg": resource_group,
                                               "vnet": vnet_name, "rt": route_table_name,
                                               "location": before["location"],
                                               "routes": before["routes"],
                                               "assigned_subnets": cleared}}
        return res
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True,
                    "message": f"Route table '{route_table_name}' not found (already deleted)."}
        log.error("delete_spoke_route_table failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def restore_spoke_route_table(subscription_id: str, resource_group: str, vnet_name: str,
                              route_table_name: str, location: str,
                              routes: list, assigned_subnets: list) -> dict:
    """Recreate a deleted spoke route table (routes + subnet associations)."""
    try:
        client = _network_client(subscription_id)
        loc = location or cfg.DEFAULT_AZURE_REGION
        log.info("Restoring route table '%s' in %s", route_table_name, resource_group)
        result = client.route_tables.begin_create_or_update(
            resource_group, route_table_name,
            {"location": loc, "properties": {"disableBgpRoutePropagation": True}}).result()
        for r in routes:
            params = {"address_prefix": r["prefix"], "next_hop_type": r.get("next_hop_type") or "VirtualAppliance"}
            if r.get("next_hop_ip"):
                params["next_hop_ip_address"] = r["next_hop_ip"]
            client.routes.begin_create_or_update(
                resource_group, route_table_name, r["name"], params).result()
        reassigned, failed = [], []
        for sname in (assigned_subnets or []):
            try:
                subnet = client.subnets.get(resource_group, vnet_name, sname)
                subnet.route_table = {"id": result.id}
                client.subnets.begin_create_or_update(
                    resource_group, vnet_name, sname, subnet).result()
                reassigned.append(sname)
            except Exception:
                failed.append(sname)
        msg = (f"Route table '{route_table_name}' restored with {len(routes)} route(s)"
               + (f", re-assigned to {', '.join(reassigned)}" if reassigned else "")
               + (f" (could not re-assign: {', '.join(failed)})" if failed else "") + ".")
        return {"success": not failed, "message": msg}
    except Exception as exc:
        log.error("restore_spoke_route_table failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── Allow internet egress on the firewall policy for a spoke ───────────────

@_guard
def allow_internet_rule(spoke_address_space: str, rule_name: str) -> dict:
    """Add a firewall network rule permitting the spoke to reach the internet."""
    return add_firewall_network_rule(
        rule_name=rule_name,
        destination_addresses=["*"],
        destination_ports=["*"],
        protocol="Any",
        source_addresses=[spoke_address_space],
    )


# ── 1. Peer spoke VNET to hub ──────────────────────────────────────────────

@_guard
def peer_hub_vnet(
    spoke_subscription_id: str,
    spoke_resource_group: str,
    spoke_vnet_name: str,
    spoke_address_space: str,
    allow_vnet_access: bool = None,
    allow_forwarded_traffic: bool = None,
    allow_gateway_transit: bool = None,
    use_remote_gateways: bool = None,
    spoke_to_hub_name: str = None,
    hub_to_spoke_name: str = None,
    on_conflict: str = None,        # "replace" = overwrite existing peerings after confirmation
) -> dict:
    """
    Creates VNET peering in both directions (spoke→hub, hub→spoke).
    If peering settings are None, falls back to env var defaults; peering
    names default to the naming templates but can be overridden per call.
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

        s2h = spoke_to_hub_name or render_name("TPL_PEERING_SPOKE_TO_HUB", vnet=spoke_vnet_name)
        h2s = hub_to_spoke_name or render_name("TPL_PEERING_HUB_TO_SPOKE", vnet=spoke_vnet_name)

        # No silent overwrite: creating over an existing peering replaces its
        # settings — surface the current state unless explicitly told to replace.
        if on_conflict != "replace":
            existing = []
            for client_, rg_, vnet_, name_, side in (
                    (spoke_client, spoke_resource_group, spoke_vnet_name, s2h, "spoke → hub"),
                    (hub_client, cfg.HUB_RESOURCE_GROUP, cfg.HUB_VNET_NAME, h2s, "hub → spoke")):
                try:
                    p = client_.virtual_network_peerings.get(rg_, vnet_, name_)
                    existing.append({
                        "name": p.name, "side": side,
                        "state": str(p.peering_state or ""),
                        "remote": (p.remote_virtual_network.id.split("/")[-1]
                                   if p.remote_virtual_network and p.remote_virtual_network.id else ""),
                        "allow_forwarded_traffic": bool(p.allow_forwarded_traffic),
                        "allow_vnet_access": bool(p.allow_virtual_network_access)})
                except Exception as exc:
                    if not _is_not_found(exc):
                        raise
            if existing:
                return {"success": False, "conflict": True, "existing_peerings": existing,
                        "message": "Peering(s) with these names already exist — creating again "
                                   "would overwrite their settings: "
                                   + "; ".join(f"{e['name']} ({e['side']}, {e['state']}, "
                                               f"remote {e['remote']})" for e in existing)}

        # Spoke → Hub
        log.info("Creating spoke→hub peering '%s' (%s → %s)", s2h, spoke_vnet_name, cfg.HUB_VNET_NAME)
        spoke_client.virtual_network_peerings.begin_create_or_update(
            resource_group_name=spoke_resource_group,
            virtual_network_name=spoke_vnet_name,
            virtual_network_peering_name=s2h,
            virtual_network_peering_parameters={
                "allow_virtual_network_access": allow_vnet_access,
                "allow_forwarded_traffic":      allow_forwarded_traffic,
                "allow_gateway_transit":        False,  # spoke never grants transit
                "use_remote_gateways":          use_remote_gateways,
                "remote_virtual_network":       {"id": hub_vnet_id},
            },
        ).result()

        # Hub → Spoke
        log.info("Creating hub→spoke peering '%s' (%s → %s)", h2s, cfg.HUB_VNET_NAME, spoke_vnet_name)
        hub_client.virtual_network_peerings.begin_create_or_update(
            resource_group_name=cfg.HUB_RESOURCE_GROUP,
            virtual_network_name=cfg.HUB_VNET_NAME,
            virtual_network_peering_name=h2s,
            virtual_network_peering_parameters={
                "allow_virtual_network_access": allow_vnet_access,
                "allow_forwarded_traffic":      allow_forwarded_traffic,
                "allow_gateway_transit":        allow_gateway_transit,
                "use_remote_gateways":          False,
                "remote_virtual_network":       {"id": spoke_vnet_id},
            },
        ).result()

        return {"success": True,
                "spoke_to_hub_name": s2h, "hub_to_spoke_name": h2s,
                "change": {"target": f"peering {spoke_vnet_name} ↔ {cfg.HUB_VNET_NAME}",
                           "before": None,
                           "after": {"spoke_to_hub": s2h, "hub_to_spoke": h2s},
                           "revert_op": "delete_peerings",
                           "revert_params": {"sub": spoke_subscription_id,
                                             "rg": spoke_resource_group,
                                             "vnet": spoke_vnet_name,
                                             "s2h": s2h, "h2s": h2s}},
                "message": f"Peering created between {spoke_vnet_name} and {cfg.HUB_VNET_NAME} "
                           f"('{s2h}' / '{h2s}')."}

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

@_guard
def create_route_table(
    name: str,
    resource_group: str,
    location: str = None,
    subscription_id: str = None,
    disable_bgp_route_propagation: bool = True,
    on_conflict: str = None,        # "keep" = reuse the existing table untouched
) -> dict:
    """Create a new route table (UDR) in the given subscription/RG."""
    try:
        sub = subscription_id or cfg.SPOKE_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID
        loc = location or cfg.DEFAULT_AZURE_REGION
        client = _network_client(sub)
        # No silent overwrite: a PUT on an existing table would clear its routes.
        try:
            existing = client.route_tables.get(resource_group, name)
            routes = [{"name": r.name, "prefix": r.address_prefix,
                       "next_hop_type": str(r.next_hop_type or ""),
                       "next_hop_ip": r.next_hop_ip_address or ""}
                      for r in (existing.routes or [])]
            if on_conflict == "keep":
                return {"success": True, "kept_existing": True, "id": existing.id,
                        "name": existing.name,
                        "message": f"Route table '{name}' already exists "
                                   f"({len(routes)} route(s)) — reusing it as-is."}
            return {"success": False, "conflict": True,
                    "existing_route_table": {"name": name, "routes": routes},
                    "message": f"Route table '{name}' already exists in {resource_group} "
                               f"with {len(routes)} route(s) — recreating it would clear them."}
        except Exception as exc:
            if not _is_not_found(exc):
                raise
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


@_guard
def add_route_to_table(
    route_table_name: str,
    resource_group: str,
    route_name: str,
    address_prefix: str,
    next_hop_type: str,
    next_hop_ip: str = None,
    subscription_id: str = None,
    on_conflict: str = None,        # None = report conflicts; "replace" = update the existing route
) -> dict:
    """
    Add a single route to a route table. Azure forbids two routes with the
    same address prefix — when another route already covers the prefix, this
    returns a conflict (with the existing route) unless on_conflict='replace',
    which updates that existing route in place to the requested next hop.
    """
    import ipaddress
    try:
        sub = subscription_id or cfg.SPOKE_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID
        client = _network_client(sub)

        def _norm(p):
            try:
                return str(ipaddress.ip_network(str(p), strict=False))
            except ValueError:
                return str(p)

        def _route_dict(r):
            return {"name": r.name, "prefix": r.address_prefix,
                    "next_hop_type": str(r.next_hop_type or ""),
                    "next_hop_ip": r.next_hop_ip_address or "",
                    "table": route_table_name}

        replaced, before = None, None
        try:
            rt = client.route_tables.get(resource_group, route_table_name)
            routes = list(rt.routes or [])
        except Exception as exc:
            if not _is_not_found(exc):
                raise
            routes = []
        # Conflict 1: another route already covers this prefix (Azure rejects it)
        existing = next((r for r in routes
                         if r.name != route_name and r.address_prefix
                         and _norm(r.address_prefix) == _norm(address_prefix)), None)
        # Conflict 2: a route with OUR name exists with different values —
        # create_or_update would silently overwrite it. Never do that by default.
        same_name = next((r for r in routes if r.name == route_name), None)
        if existing is None and same_name is not None and (
                _norm(same_name.address_prefix or "") != _norm(address_prefix)
                or (same_name.next_hop_ip_address or "") != (next_hop_ip or "")):
            existing = same_name
        if existing is not None:
            if on_conflict == "replace":
                replaced = existing.name
                before = _route_dict(existing)
                route_name = existing.name          # update the conflicting route in place
            else:
                return {"success": False, "conflict": True,
                        "existing_route": _route_dict(existing),
                        "message": f"Route '{existing.name}' already exists in "
                                   f"'{route_table_name}' with a conflicting definition "
                                   f"({existing.address_prefix}) — updating it would overwrite "
                                   f"the current route."}

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
        after = {"name": route_name, "prefix": address_prefix,
                 "next_hop_type": next_hop_type, "next_hop_ip": next_hop_ip or "",
                 "table": route_table_name}
        if replaced:
            return {"success": True, "replaced_existing": True,
                    "change": {"target": f"route {route_name} @ {route_table_name}",
                               "before": before, "after": after,
                               "revert_op": "restore_route",
                               "revert_params": {"table": route_table_name, "rg": resource_group,
                                                 "route": before, "sub": sub}},
                    "message": f"Existing route '{replaced}' updated in place "
                               f"({address_prefix} → {next_hop_type}"
                               f"{' ' + next_hop_ip if next_hop_ip else ''}) in {route_table_name}."}
        return {"success": True,
                "change": {"target": f"route {route_name} @ {route_table_name}",
                           "before": None, "after": after,
                           "revert_op": "delete_route",
                           "revert_params": {"table": route_table_name, "rg": resource_group,
                                             "route_name": route_name, "sub": sub}},
                "message": f"Route '{route_name}' ({address_prefix} → {next_hop_type}) added to {route_table_name}."}
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


@_guard
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

@_guard
def assign_route_table_to_subnet(
    subscription_id: str,
    resource_group: str,
    vnet_name: str,
    subnet_name: str,
    route_table_id: str,            # None/"" clears the association (used by revert)
) -> dict:
    """Associate a route table (UDR) with a subnet; the previous association
    is snapshotted so the change can be reverted."""
    try:
        client = _network_client(subscription_id)
        subnet = client.subnets.get(resource_group, vnet_name, subnet_name)
        prev_id = subnet.route_table.id if subnet.route_table else None
        subnet.route_table = {"id": route_table_id} if route_table_id else None
        log.info("Assigning UDR %s to subnet %s/%s", route_table_id or "(none)",
                 vnet_name, subnet_name)
        client.subnets.begin_create_or_update(
            resource_group, vnet_name, subnet_name, subnet
        ).result()
        new_name = route_table_id.split("/")[-1] if route_table_id else None
        prev_name = prev_id.split("/")[-1] if prev_id else None
        return {"success": True,
                "change": {"target": f"subnet {vnet_name}/{subnet_name} UDR association",
                           "before": {"route_table": prev_name, "route_table_id": prev_id},
                           "after": {"route_table": new_name, "route_table_id": route_table_id},
                           "revert_op": "assign_subnet_rt",
                           "revert_params": {"sub": subscription_id, "rg": resource_group,
                                             "vnet": vnet_name, "subnet": subnet_name,
                                             "rt_id": prev_id}},
                "message": (f"UDR {'assigned to' if route_table_id else 'cleared from'} subnet "
                            f"'{subnet_name}'"
                            + (f" (replaced previous UDR '{prev_name}')."
                               if prev_id and route_table_id else "."))}
    except Exception as exc:
        log.error("assign_route_table_to_subnet failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── Private DNS zones (hub) ─────────────────────────────────────────────────

def check_private_dns_zone(zone_name: str) -> dict:
    """
    Read-only: does the hub own this private DNS zone? Uses the generic
    resource listing (no extra SDK needed). Never mutates anything.
    """
    zone_name = str(zone_name or "").strip().lower().rstrip(".")
    if not zone_name or "." not in zone_name:
        return {"success": False, "message": "Enter a valid DNS zone name, e.g. contoso.internal."}
    rg = cfg.DNS_ZONE_RG
    if not rg:
        return {"success": False,
                "message": "Hub private DNS zones resource group not configured "
                           "(Settings → Hub & Subscriptions)."}
    try:
        sub = cfg.DNS_ZONE_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID
        client = _resource_client(sub)
        zones = [r.name for r in client.resources.list_by_resource_group(
            rg, filter="resourceType eq 'Microsoft.Network/privateDnsZones'")]
        exists = zone_name in {z.lower() for z in zones}
        return {"success": True, "exists": exists, "zone": zone_name,
                "message": (f"Zone '{zone_name}' EXISTS in the hub ({rg})."
                            if exists else
                            f"Zone '{zone_name}' is NOT present in the hub ({rg}).")}
    except Exception as exc:
        log.error("check_private_dns_zone failed: %s", exc)
        return {"success": False, "exists": None,
                "message": f"Could not verify zone availability: {exc}"}


# ── Revert / decommission helpers ──────────────────────────────────────────
# Deletions treat "not found" as success so reverts are idempotent.

def _is_not_found(exc) -> bool:
    try:
        from azure.core.exceptions import ResourceNotFoundError
        if isinstance(exc, ResourceNotFoundError):
            return True
    except ImportError:
        pass
    return "NotFound" in str(exc) or "was not found" in str(exc)


@_guard
def delete_route_from_table(
    route_table_name: str,
    resource_group: str,
    route_name: str,
    subscription_id: str = None,
) -> dict:
    """Delete a single named route (its definition is snapshotted for revert)."""
    try:
        sub = subscription_id or cfg.SPOKE_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID
        client = _network_client(sub)
        before = None
        try:
            r = client.routes.get(resource_group, route_table_name, route_name)
            before = {"name": r.name, "prefix": r.address_prefix,
                      "next_hop_type": str(r.next_hop_type or ""),
                      "next_hop_ip": r.next_hop_ip_address or "",
                      "table": route_table_name}
        except Exception as exc:
            if not _is_not_found(exc):
                raise
        log.info("Deleting route '%s' from table '%s'", route_name, route_table_name)
        client.routes.begin_delete(resource_group, route_table_name, route_name).result()
        res = {"success": True, "message": f"Route '{route_name}' removed from {route_table_name}."}
        if before:
            res["change"] = {"target": f"route {route_name} @ {route_table_name}",
                             "before": before, "after": None,
                             "revert_op": "restore_route",
                             "revert_params": {"table": route_table_name, "rg": resource_group,
                                               "route": before, "sub": sub}}
        return res
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True,
                    "message": f"Route '{route_name}' not present in {route_table_name} (already removed)."}
        log.error("delete_route_from_table failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def remove_routes_by_prefix(
    route_table_name: str,
    resource_group: str,
    address_prefix: str,
    subscription_id: str = None,
) -> dict:
    """Delete every route in a table whose address prefix matches (e.g. a spoke CIDR)."""
    try:
        sub = subscription_id or cfg.HUB_SUBSCRIPTION_ID
        client = _network_client(sub)
        rt = client.route_tables.get(resource_group, route_table_name)
        matched = [r for r in (rt.routes or []) if r.address_prefix == address_prefix]
        removed = []
        for r in matched:
            log.info("Deleting route '%s' (%s) from '%s'", r.name, address_prefix, route_table_name)
            client.routes.begin_delete(resource_group, route_table_name, r.name).result()
            removed.append({"name": r.name, "prefix": r.address_prefix,
                            "next_hop_type": str(r.next_hop_type or ""),
                            "next_hop_ip": r.next_hop_ip_address or "",
                            "table": route_table_name})
        if removed:
            return {"success": True,
                    "change": {"target": f"{len(removed)} route(s) @ {route_table_name}",
                               "before": removed, "after": None,
                               "revert_op": "restore_routes",
                               "revert_params": {"table": route_table_name, "rg": resource_group,
                                                 "routes": removed, "sub": sub}},
                    "message": f"Removed {len(removed)} route(s) for {address_prefix} from "
                               f"{route_table_name}: {', '.join(r['name'] for r in removed)}."}
        return {"success": True,
                "message": f"No routes for {address_prefix} in {route_table_name} (nothing to remove)."}
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True, "message": f"Route table '{route_table_name}' not found (nothing to remove)."}
        log.error("remove_routes_by_prefix failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def remove_firewall_rule(rule_name: str, rcg_name: str = None,
                         collection_name: str = None) -> dict:
    """
    Remove a named rule from the policy's filter rule collections.
    Searches the given RCG (or every RCG when unspecified); not-found = success.
    """
    try:
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        for rcg in _iter_rcgs(client, rcg_name):
            removed_def, removed_col = None, None
            for rc in (rcg.rule_collections or []):
                if rc.rule_collection_type != "FirewallPolicyFilterRuleCollection":
                    continue
                if collection_name and rc.name != collection_name:
                    continue
                hit = next((r for r in (rc.rules or []) if r.name == rule_name), None)
                if hit is not None:
                    removed_def, removed_col = _describe_fw_rule(hit, rc.name), rc.name
                    rc.rules = [r for r in (rc.rules or []) if r.name != rule_name]
            if removed_def:
                log.info("Removing firewall rule '%s' from RCG '%s'", rule_name, rcg.name)
                client.firewall_policy_rule_collection_groups.begin_create_or_update(
                    cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME, rcg.name, rcg,
                ).result()
                return {"success": True,
                        "change": {"target": f"fw rule {rule_name} @ {rcg.name}/{removed_col}",
                                   "before": removed_def, "after": None,
                                   "revert_op": "restore_fw_rule",
                                   "revert_params": {"rule": removed_def, "rcg": rcg.name,
                                                     "collection": removed_col}},
                        "message": f"Firewall rule '{rule_name}' removed (RCG '{rcg.name}')."}
        return {"success": True, "message": f"Firewall rule '{rule_name}' not present (already removed)."}
    except Exception as exc:
        log.error("remove_firewall_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def delete_hub_spoke_peerings(
    spoke_subscription_id: str,
    spoke_resource_group: str,
    spoke_vnet_name: str,
    spoke_to_hub_name: str = None,
    hub_to_spoke_name: str = None,
) -> dict:
    """Delete both peering directions (spoke→hub and hub→spoke)."""
    results = []
    # Hub side first — it survives even if the spoke VNET was already deleted
    try:
        hub_client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        name = hub_to_spoke_name or render_name("TPL_PEERING_HUB_TO_SPOKE", vnet=spoke_vnet_name)
        log.info("Deleting hub→spoke peering '%s'", name)
        hub_client.virtual_network_peerings.begin_delete(
            cfg.HUB_RESOURCE_GROUP, cfg.HUB_VNET_NAME, name).result()
        results.append(f"hub→spoke '{name}' deleted")
    except Exception as exc:
        if _is_not_found(exc):
            results.append("hub→spoke peering already absent")
        else:
            log.error("delete hub→spoke peering failed: %s", exc)
            return {"success": False, "message": f"Hub-side peering delete failed: {exc}"}
    try:
        spoke_client = _network_client(spoke_subscription_id)
        name = spoke_to_hub_name or render_name("TPL_PEERING_SPOKE_TO_HUB", vnet=spoke_vnet_name)
        log.info("Deleting spoke→hub peering '%s'", name)
        spoke_client.virtual_network_peerings.begin_delete(
            spoke_resource_group, spoke_vnet_name, name).result()
        results.append(f"spoke→hub '{name}' deleted")
    except Exception as exc:
        if _is_not_found(exc):
            results.append("spoke→hub peering already absent")
        else:
            log.error("delete spoke→hub peering failed: %s", exc)
            return {"success": False, "message": f"Spoke-side peering delete failed: {exc}"}
    s2h_final = spoke_to_hub_name or render_name("TPL_PEERING_SPOKE_TO_HUB", vnet=spoke_vnet_name)
    h2s_final = hub_to_spoke_name or render_name("TPL_PEERING_HUB_TO_SPOKE", vnet=spoke_vnet_name)
    return {"success": True,
            "change": {"target": f"peering {spoke_vnet_name} ↔ {cfg.HUB_VNET_NAME}",
                       "before": {"spoke_to_hub": s2h_final, "hub_to_spoke": h2s_final},
                       "after": None,
                       "revert_op": "restore_peerings",
                       "revert_params": {"sub": spoke_subscription_id, "rg": spoke_resource_group,
                                         "vnet": spoke_vnet_name,
                                         "s2h": s2h_final, "h2s": h2s_final}},
            "message": "Peerings removed: " + "; ".join(results) + "."}


@_guard
def delete_spoke_vnet(subscription_id: str, resource_group: str, vnet_name: str) -> dict:
    """Delete a spoke VNET; its network definition is snapshotted for revert.
    (Fails in Azure if any subnet still has attached devices.)"""
    try:
        client = _network_client(subscription_id)
        before = None
        try:
            v = client.virtual_networks.get(resource_group, vnet_name)
            before = {"name": v.name, "location": v.location,
                      "address_space": list(v.address_space.address_prefixes or []),
                      "subnets": [{"name": s.name,
                                   "prefix": s.address_prefix or ", ".join(
                                       getattr(s, "address_prefixes", None) or [])}
                                  for s in (v.subnets or [])]}
        except Exception as exc:
            if not _is_not_found(exc):
                raise
        log.info("Deleting VNET '%s' in %s", vnet_name, resource_group)
        client.virtual_networks.begin_delete(resource_group, vnet_name).result()
        res = {"success": True, "message": f"VNET '{vnet_name}' deleted."}
        if before:
            res["change"] = {"target": f"VNET {vnet_name} @ {resource_group}",
                             "before": before, "after": None,
                             "revert_op": "restore_vnet",
                             "revert_params": {"sub": subscription_id, "rg": resource_group,
                                               "vnet": vnet_name, "location": before["location"],
                                               "address_space": before["address_space"],
                                               "subnets": before["subnets"]}}
        return res
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True, "message": f"VNET '{vnet_name}' not found (already deleted)."}
        log.error("delete_spoke_vnet failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def restore_vnet(subscription_id: str, resource_group: str, vnet_name: str,
                 location: str, address_space: list, subnets: list) -> dict:
    """Recreate a VNET from a stored snapshot (network definition only —
    attached devices/NSGs from before a decommission cannot be restored)."""
    try:
        client = _network_client(subscription_id)
        log.info("Restoring VNET '%s' in %s", vnet_name, resource_group)
        client.virtual_networks.begin_create_or_update(
            resource_group, vnet_name,
            {"location": location,
             "address_space": {"address_prefixes": address_space},
             "subnets": [{"name": s["name"], "address_prefix": s["prefix"].split(",")[0].strip()}
                         for s in subnets if s.get("prefix")]},
        ).result()
        return {"success": True,
                "message": f"VNET '{vnet_name}' restored ({', '.join(address_space)}, "
                           f"{len(subnets)} subnet(s)). Attached devices/NSGs are NOT restored."}
    except Exception as exc:
        log.error("restore_vnet failed: %s", exc)
        return {"success": False, "message": str(exc)}


def decommission_check(subscription_id: str, resource_group: str, vnet_name: str) -> dict:
    """
    Read-only pre-decommission report: does the VNET exist, what peerings does it
    have, and do any subnets still have attached devices (NIC ip-configurations)?
    Runs for real even in dry-run mode — it never mutates anything.
    """
    try:
        client = _network_client(subscription_id)
        try:
            vnet = client.virtual_networks.get(resource_group, vnet_name)
        except Exception as exc:
            if _is_not_found(exc):
                return {"success": True, "exists": False, "clear": True,
                        "peerings": [], "subnets": [], "total_devices": 0,
                        "message": f"VNET '{vnet_name}' not found in {resource_group} — already deleted?"}
            raise
        peerings = [{"name": p.name,
                     "state": str(p.peering_state or ""),
                     "remote": (p.remote_virtual_network.id.split("/")[-1]
                                if p.remote_virtual_network and p.remote_virtual_network.id else "")}
                    for p in client.virtual_network_peerings.list(resource_group, vnet_name)]
        subnets, total_devices = [], 0
        for s in client.subnets.list(resource_group, vnet_name):
            devices = len(s.ip_configurations or [])
            total_devices += devices
            prefix = s.address_prefix or ", ".join(getattr(s, "address_prefixes", None) or [])
            subnets.append({"name": s.name, "prefix": prefix,
                            "devices": devices, "has_udr": s.route_table is not None})
        spaces = ", ".join(vnet.address_space.address_prefixes or [])
        return {
            "success": True, "exists": True, "address_space": spaces,
            "peerings": peerings, "subnets": subnets, "total_devices": total_devices,
            "clear": total_devices == 0,
            "message": (f"VNET '{vnet_name}' ({spaces}): {len(peerings)} peering(s), "
                        f"{len(subnets)} subnet(s), {total_devices} attached device(s). "
                        + ("Clear to decommission." if total_devices == 0
                           else "NOT clear — devices are still attached; ask the requester to remove them.")),
        }
    except Exception as exc:
        log.error("decommission_check failed: %s", exc)
        return {"success": False, "exists": None, "clear": False, "message": str(exc)}


# ── Firewall policy lifecycle: status / create-if-missing / modify ─────────

def _describe_fw_rule(rule, collection_name: str) -> dict:
    """Serialize an SDK rule object for display in the admin UI."""
    d = {"name": rule.name, "collection": collection_name,
         "kind": "application" if rule.rule_type == "ApplicationRule" else "network",
         "sources": list(rule.source_addresses or [])}
    if rule.rule_type == "ApplicationRule":
        d["fqdns"] = list(rule.target_fqdns or [])
        d["protocols"] = [f"{p.protocol_type}/{p.port}" for p in (rule.protocols or [])]
    else:
        d["destinations"] = list(rule.destination_addresses or [])
        d["ports"] = list(rule.destination_ports or [])
        d["protocols"] = list(rule.ip_protocols or [])
    return d


# ── Coverage matching: is requested traffic already allowed/denied? ────────

def _addr_covers(existing_list, requested) -> bool:
    """Does any existing address entry ('*' or a CIDR/IP superset) cover the request?"""
    import ipaddress
    requested = str(requested).strip()
    existing = [str(e).strip() for e in (existing_list or [])]
    if requested == "*":
        return any(e in ("*", "0.0.0.0/0") for e in existing)
    try:
        req = ipaddress.ip_network(requested, strict=False)
    except ValueError:
        return False
    for e in existing:
        if e == "*":
            return True
        try:
            if req.subnet_of(ipaddress.ip_network(e, strict=False)):
                return True
        except ValueError:
            continue
    return False


def _fqdn_covers(existing_list, requested) -> bool:
    """'*' covers all; '*.example.com' covers sub.example.com (and narrower wildcards)."""
    req = str(requested).strip().lower()
    for e in (existing_list or []):
        e = str(e).strip().lower()
        if e == "*" or e == req:
            return True
        if e.startswith("*."):
            suffix = e[1:]                       # ".example.com"
            if req.startswith("*."):
                if req == e or req[1:].endswith(suffix):
                    return True
            elif req.endswith(suffix):
                return True
    return False


def _ports_cover(existing_ports, requested_ports) -> bool:
    ranges = []
    for p in (existing_ports or []):
        p = str(p).strip()
        if p == "*":
            return True
        if "-" in p:
            a, _, b = p.partition("-")
            if a.strip().isdigit() and b.strip().isdigit():
                ranges.append((int(a), int(b)))
        elif p.isdigit():
            ranges.append((int(p), int(p)))
    for rp in (requested_ports or []):
        rp = str(rp).strip()
        if not rp.isdigit():                     # '*' or unknown → needs existing '*'
            return False
        if not any(a <= int(rp) <= b for a, b in ranges):
            return False
    return True


def _ip_protocols_cover(existing, requested) -> bool:
    ex = {str(p).lower() for p in (existing or [])}
    if "any" in ex:
        return True
    return all(str(p).lower() in ex and str(p).lower() != "any" for p in (requested or []))


def _rule_covers(rule, kind: str, sources, dest, ports, ip_protocols, app_protocols) -> bool:
    """Would this existing rule match ALL requested sources → this one destination?"""
    rule_kind = "application" if rule.rule_type == "ApplicationRule" else "network"
    if rule_kind != kind:
        return False
    if not all(_addr_covers(rule.source_addresses, s) for s in (sources or ["*"])):
        return False
    if kind == "application":
        if not _fqdn_covers(rule.target_fqdns, dest):
            return False
        have = {(str(p.protocol_type).lower(), int(p.port)) for p in (rule.protocols or [])}
        return all((str(p["protocol_type"]).lower(), int(p.get("port", 443))) in have
                   for p in (app_protocols or []))
    if not _addr_covers(rule.destination_addresses, dest):
        return False
    return (_ports_cover(rule.destination_ports, ports)
            and _ip_protocols_cover(rule.ip_protocols, ip_protocols))


def _analyze_coverage(rcgs, cov: dict) -> dict:
    """
    Per requested destination, find matching rules across every filter
    collection and resolve the effective action the way Azure Firewall does:
    lowest RCG priority, then lowest collection priority wins.
    """
    results, fully = [], True
    for dest in cov.get("dests", []):
        matches = []
        for g in rcgs:
            for rc in (g.rule_collections or []):
                if rc.rule_collection_type != "FirewallPolicyFilterRuleCollection":
                    continue
                action = getattr(getattr(rc, "action", None), "type", "") or "Allow"
                for r in (rc.rules or []):
                    if _rule_covers(r, cov["kind"], cov.get("sources"), dest,
                                    cov.get("ports"), cov.get("ip_protocols"),
                                    cov.get("app_protocols")):
                        matches.append({"rule": r.name, "collection": rc.name, "rcg": g.name,
                                        "action": action,
                                        "_prio": (g.priority or 65000, rc.priority or 65000)})
        matches.sort(key=lambda m: m["_prio"])
        effective = matches[0] if matches else None
        for m in matches:
            m.pop("_prio", None)
        covered = bool(effective and effective["action"] == "Allow")
        fully = fully and covered
        results.append({"dest": dest, "covered": covered,
                        "denied": bool(effective and effective["action"] == "Deny"),
                        "effective": effective, "matches": matches})
    n_ok = sum(1 for r in results if r["covered"])
    return {"evaluated": True, "kind": cov["kind"], "fully_covered": fully and bool(results),
            "results": results,
            "message": (f"All {len(results)} requested destination(s) are already allowed by "
                        f"existing rules — no new rule needed." if fully and results else
                        f"{n_ok}/{len(results)} requested destination(s) already allowed.")}


def get_firewall_policy_status(rule_name: str = None, coverage: dict = None) -> dict:
    """
    Read-only firewall report for the admin UI: does the policy exist, what
    rule collection groups / collections / rules does it hold (across ALL
    RCGs), and — if rule_name is given — the current definition of that rule.
    Runs for real even in dry-run mode (never mutates).
    """
    if not cfg.FIREWALL_POLICY_NAME or not cfg.FIREWALL_POLICY_RG:
        return {"success": False,
                "message": "Firewall policy name / resource group not configured in Settings → Firewall."}
    out = {"success": True, "policy_exists": False, "rcg_exists": False,
           "policy": cfg.FIREWALL_POLICY_NAME, "rcg": cfg.FIREWALL_RULE_COLLECTION_GROUP,
           "collections": [], "rule": None}
    try:
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        try:
            client.firewall_policies.get(cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME)
            out["policy_exists"] = True
        except Exception as exc:
            if _is_not_found(exc):
                out["message"] = (f"Firewall policy '{cfg.FIREWALL_POLICY_NAME}' does NOT exist "
                                  f"in {cfg.FIREWALL_POLICY_RG} — use 'Create Policy' to create it.")
                return out
            raise
        rcgs = list(client.firewall_policy_rule_collection_groups.list(
            cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME))
        out["rcg_exists"] = any(g.name == cfg.FIREWALL_RULE_COLLECTION_GROUP for g in rcgs)
        for g in rcgs:
            for rc in (g.rule_collections or []):
                is_filter = rc.rule_collection_type == "FirewallPolicyFilterRuleCollection"
                # Azure forbids mixing network and application rules in one collection —
                # expose the collection's current rule type so the UI can match it.
                kinds = {("application" if r.rule_type == "ApplicationRule" else "network")
                         for r in (rc.rules or [])}
                out["collections"].append({
                    "rcg": g.name, "rcg_priority": g.priority,
                    "name": rc.name, "priority": rc.priority,
                    "action": getattr(getattr(rc, "action", None), "type", "") or "",
                    "filter": is_filter, "rules": [r.name for r in (rc.rules or [])],
                    "rule_type": kinds.pop() if len(kinds) == 1 else ("mixed" if kinds else ""),
                })
                if rule_name and is_filter and out["rule"] is None:
                    for r in (rc.rules or []):
                        if r.name == rule_name:
                            out["rule"] = _describe_fw_rule(r, rc.name)
                            out["rule"]["rcg"] = g.name
        if coverage and coverage.get("dests"):
            out["coverage"] = _analyze_coverage(rcgs, coverage)
        total = sum(len(c["rules"]) for c in out["collections"])
        out["message"] = (f"Policy '{cfg.FIREWALL_POLICY_NAME}' OK — {len(rcgs)} rule collection "
                          f"group(s), {len(out['collections'])} collection(s), {total} rule(s)."
                          + (f" Rule '{rule_name}' " + ("found." if out["rule"] else "NOT found.")
                             if rule_name else ""))
        return out
    except Exception as exc:
        log.error("get_firewall_policy_status failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def ensure_firewall_policy(location: str = None, rcg_name: str = None,
                           collection_name: str = None, rcg_priority: int = 200,
                           collection_priority: int = 200, action: str = "Allow") -> dict:
    """
    Create whatever is missing so rules can be managed: the firewall policy,
    the given rule collection group, and the given filter rule collection.
    Existing pieces are left untouched.
    """
    try:
        loc = location or cfg.DEFAULT_AZURE_REGION
        rcg_name = rcg_name or cfg.FIREWALL_RULE_COLLECTION_GROUP
        collection_name = collection_name or "app-managed-rules"
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        steps = []

        try:
            client.firewall_policies.get(cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME)
            steps.append(f"policy '{cfg.FIREWALL_POLICY_NAME}' exists")
        except Exception as exc:
            if not _is_not_found(exc):
                raise
            log.info("Creating firewall policy '%s'", cfg.FIREWALL_POLICY_NAME)
            client.firewall_policies.begin_create_or_update(
                cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME,
                {"location": loc, "sku": {"tier": "Standard"}},
            ).result()
            steps.append(f"policy '{cfg.FIREWALL_POLICY_NAME}' created")

        rcg = None
        try:
            rcg = client.firewall_policy_rule_collection_groups.get(
                cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME, rcg_name)
            steps.append(f"rule collection group '{rcg_name}' exists")
        except Exception as exc:
            if not _is_not_found(exc):
                raise

        filter_collection = {
            "rule_collection_type": "FirewallPolicyFilterRuleCollection",
            "name": collection_name, "priority": int(collection_priority),
            "action": {"type": action or "Allow"}, "rules": [],
        }
        if rcg is None:
            log.info("Creating rule collection group '%s'", rcg_name)
            client.firewall_policy_rule_collection_groups.begin_create_or_update(
                cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME, rcg_name,
                {"priority": int(rcg_priority), "rule_collections": [filter_collection]},
            ).result()
            steps.append(f"rule collection group '{rcg_name}' + collection '{collection_name}' created")
        elif not any(rc.name == collection_name for rc in (rcg.rule_collections or [])):
            rcg.rule_collections = list(rcg.rule_collections or []) + [filter_collection]
            client.firewall_policy_rule_collection_groups.begin_create_or_update(
                cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME, rcg_name, rcg,
            ).result()
            steps.append(f"filter collection '{collection_name}' added to '{rcg_name}'")
        else:
            steps.append(f"collection '{collection_name}' exists in '{rcg_name}'")

        return {"success": True, "message": "Firewall ready: " + "; ".join(steps) + "."}
    except Exception as exc:
        log.error("ensure_firewall_policy failed: %s", exc)
        return {"success": False, "message": str(exc)}


def _build_fw_rule(rule_name, rule_kind, source_addresses, destinations,
                   ports=None, ip_protocols=None, app_protocols=None):
    from azure.mgmt.network.models import (NetworkRule, ApplicationRule,
                                           FirewallPolicyRuleApplicationProtocol)
    if rule_kind == "application":
        return ApplicationRule(
            name=rule_name, rule_type="ApplicationRule",
            source_addresses=source_addresses or ["*"],
            target_fqdns=destinations,
            protocols=[FirewallPolicyRuleApplicationProtocol(
                protocol_type=p["protocol_type"], port=p.get("port", 443))
                for p in (app_protocols or [{"protocol_type": "Https", "port": 443}])],
        )
    return NetworkRule(
        name=rule_name, rule_type="NetworkRule",
        ip_protocols=ip_protocols or ["Any"],
        source_addresses=source_addresses or ["*"],
        destination_addresses=destinations,
        destination_ports=ports or ["*"],
    )


def _iter_rcgs(client, rcg_name=None):
    """RCGs to search: the named one, or every RCG of the policy."""
    if rcg_name:
        return [client.firewall_policy_rule_collection_groups.get(
            cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME, rcg_name)]
    return list(client.firewall_policy_rule_collection_groups.list(
        cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME))


@_guard
def replace_firewall_rule(
    rule_name: str,
    rule_kind: str,                 # "network" | "application"
    source_addresses: list,
    destinations: list,             # IP/CIDRs (network) or FQDNs (application)
    ports: list = None,
    ip_protocols: list = None,
    app_protocols: list = None,     # [{"protocol_type": "Https", "port": 443}]
    rcg_name: str = None,           # search this RCG only (else all RCGs)
    collection_name: str = None,    # restrict to this collection
) -> dict:
    """Modify an existing rule in place (keeps its collection & position)."""
    try:
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        new_rule = _build_fw_rule(rule_name, rule_kind, source_addresses, destinations,
                                  ports=ports, ip_protocols=ip_protocols,
                                  app_protocols=app_protocols)
        for rcg in _iter_rcgs(client, rcg_name):
            found, before, found_col = False, None, None
            for rc in (rcg.rule_collections or []):
                if rc.rule_collection_type != "FirewallPolicyFilterRuleCollection":
                    continue
                if collection_name and rc.name != collection_name:
                    continue
                for i, r in enumerate(rc.rules or []):
                    if r.name == rule_name:
                        before = _describe_fw_rule(r, rc.name)
                        rc.rules[i] = new_rule
                        found, found_col = True, rc.name
            if found:
                log.info("Replacing firewall rule '%s' in RCG '%s'", rule_name, rcg.name)
                client.firewall_policy_rule_collection_groups.begin_create_or_update(
                    cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME, rcg.name, rcg,
                ).result()
                return {"success": True,
                        "change": {"target": f"fw rule {rule_name} @ {rcg.name}/{found_col}",
                                   "before": before,
                                   "after": _describe_fw_rule(new_rule, found_col),
                                   "revert_op": "restore_fw_rule",
                                   "revert_params": {"rule": before, "rcg": rcg.name,
                                                     "collection": found_col}},
                        "message": f"Rule '{rule_name}' updated in place (RCG '{rcg.name}')."}
        where = f" in {rcg_name}/{collection_name}" if (rcg_name or collection_name) else ""
        return {"success": False,
                "message": f"Rule '{rule_name}' not found{where} — cannot modify. "
                           f"Run the check to see existing rule names."}
    except Exception as exc:
        log.error("replace_firewall_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── NSG rule CIDR lists (NMO ZPA outbound allow) ────────────────────────────

def _nsg_rule_prefixes(rule) -> list:
    out = list(rule.destination_address_prefixes or [])
    if rule.destination_address_prefix:
        out.insert(0, rule.destination_address_prefix)
    return out


def get_nsg_rule_status(nsg_name: str, resource_group: str, rule_name: str,
                        subscription_id: str = None) -> dict:
    """Read-only: current definition of one NSG security rule (never mutates)."""
    if not nsg_name or not resource_group or not rule_name:
        return {"success": False,
                "message": "NMO NSG name / resource group / rule not configured in Settings → ZPA NMO."}
    try:
        client = _network_client(subscription_id or cfg.NMO_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID)
        rule = client.security_rules.get(resource_group, nsg_name, rule_name)
        prefixes = _nsg_rule_prefixes(rule)
        return {"success": True, "exists": True,
                "rule": {"name": rule.name, "direction": str(rule.direction or ""),
                         "access": str(rule.access or ""), "priority": rule.priority,
                         "protocol": str(rule.protocol or ""),
                         "sources": ([rule.source_address_prefix] if rule.source_address_prefix
                                     else list(rule.source_address_prefixes or [])),
                         "destinations": prefixes,
                         "ports": ([rule.destination_port_range] if rule.destination_port_range
                                   else list(rule.destination_port_ranges or []))},
                "message": (f"NSG rule '{rule_name}' ({rule.access} {rule.direction}, "
                            f"priority {rule.priority}) — {len(prefixes)} destination prefix(es).")}
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": False, "exists": False,
                    "message": f"NSG rule '{rule_name}' not found on '{nsg_name}' ({resource_group})."}
        log.error("get_nsg_rule_status failed: %s", exc)
        return {"success": False, "exists": None, "message": str(exc)}


@_guard
def add_cidr_to_nsg_rule(nsg_name: str, resource_group: str, rule_name: str,
                         cidr: str, subscription_id: str = None) -> dict:
    """Append a CIDR to an NSG rule's destination list (idempotent)."""
    try:
        client = _network_client(subscription_id or cfg.NMO_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID)
        rule = client.security_rules.get(resource_group, nsg_name, rule_name)
        prefixes = _nsg_rule_prefixes(rule)
        if cidr in prefixes:
            return {"success": True,
                    "message": f"{cidr} is already in NSG rule '{rule_name}' — nothing to do."}
        rule.destination_address_prefix = None      # move single-prefix form to the list form
        rule.destination_address_prefixes = prefixes + [cidr]
        log.info("Adding %s to NSG rule %s/%s", cidr, nsg_name, rule_name)
        client.security_rules.begin_create_or_update(
            resource_group, nsg_name, rule_name, rule).result()
        return {"success": True,
                "change": {"target": f"NSG rule {rule_name} @ {nsg_name}",
                           "before": prefixes, "after": prefixes + [cidr],
                           "revert_op": "remove_nsg_cidr",
                           "revert_params": {"nsg": nsg_name, "rg": resource_group,
                                             "rule": rule_name, "cidr": cidr,
                                             "sub": subscription_id or ""}},
                "message": f"{cidr} added to NSG rule '{rule_name}' "
                           f"({len(prefixes) + 1} destination prefixes now)."}
    except Exception as exc:
        log.error("add_cidr_to_nsg_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def remove_cidr_from_nsg_rule(nsg_name: str, resource_group: str, rule_name: str,
                              cidr: str, subscription_id: str = None) -> dict:
    """Remove a CIDR from an NSG rule's destination list (not-found = success)."""
    try:
        client = _network_client(subscription_id or cfg.NMO_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID)
        rule = client.security_rules.get(resource_group, nsg_name, rule_name)
        prefixes = _nsg_rule_prefixes(rule)
        if cidr not in prefixes:
            return {"success": True,
                    "message": f"{cidr} not present in NSG rule '{rule_name}' (already removed)."}
        before = list(prefixes)
        prefixes = [p for p in prefixes if p != cidr]
        rule.destination_address_prefix = None
        rule.destination_address_prefixes = prefixes
        client.security_rules.begin_create_or_update(
            resource_group, nsg_name, rule_name, rule).result()
        return {"success": True,
                "change": {"target": f"NSG rule {rule_name} @ {nsg_name}",
                           "before": before, "after": prefixes,
                           "revert_op": "add_nsg_cidr",
                           "revert_params": {"nsg": nsg_name, "rg": resource_group,
                                             "rule": rule_name, "cidr": cidr,
                                             "sub": subscription_id or ""}},
                "message": f"{cidr} removed from NSG rule '{rule_name}'."}
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True, "message": f"NSG rule '{rule_name}' not found (nothing to remove)."}
        log.error("remove_cidr_from_nsg_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── Firewall rule CIDR lists (NMO ZPA allow/deny) ───────────────────────────

def _mutate_fw_rule_destinations(rule_name: str, cidr: str, add: bool) -> dict:
    """Append/remove a CIDR on a named network rule's destination list
    (searched across every rule collection group). Idempotent both ways."""
    client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
    for rcg in _iter_rcgs(client):
        for rc in (rcg.rule_collections or []):
            if rc.rule_collection_type != "FirewallPolicyFilterRuleCollection":
                continue
            for r in (rc.rules or []):
                if r.name != rule_name or r.rule_type != "NetworkRule":
                    continue
                dests = list(r.destination_addresses or [])
                if add:
                    if cidr in dests:
                        return {"success": True, "found": True,
                                "message": f"{cidr} is already in firewall rule '{rule_name}' — nothing to do."}
                    r.destination_addresses = dests + [cidr]
                else:
                    if cidr not in dests:
                        return {"success": True, "found": True,
                                "message": f"{cidr} not present in firewall rule '{rule_name}' (already removed)."}
                    r.destination_addresses = [d for d in dests if d != cidr]
                log.info("%s %s on firewall rule %s (%s/%s)",
                         "Adding" if add else "Removing", cidr, rule_name, rcg.name, rc.name)
                client.firewall_policy_rule_collection_groups.begin_create_or_update(
                    cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME, rcg.name, rcg).result()
                return {"success": True, "found": True,
                        "change": {"target": f"fw rule {rule_name} destinations",
                                   "before": dests, "after": list(r.destination_addresses),
                                   "revert_op": "remove_fw_cidr" if add else "add_fw_cidr",
                                   "revert_params": {"rule_name": rule_name, "cidr": cidr}},
                        "message": f"{cidr} {'added to' if add else 'removed from'} firewall rule "
                                   f"'{rule_name}' ({rcg.name}/{rc.name}, "
                                   f"{len(r.destination_addresses)} destination(s) now)."}
    if not add:
        return {"success": True, "found": False,
                "message": f"Firewall rule '{rule_name}' not found (nothing to remove)."}
    return {"success": False, "found": False,
            "message": f"Firewall network rule '{rule_name}' not found in policy "
                       f"'{cfg.FIREWALL_POLICY_NAME}' — check the name in Settings → ZPA NMO."}


@_guard
def add_cidr_to_firewall_rule(rule_name: str, cidr: str) -> dict:
    try:
        return _mutate_fw_rule_destinations(rule_name, cidr, add=True)
    except Exception as exc:
        log.error("add_cidr_to_firewall_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def remove_cidr_from_firewall_rule(rule_name: str, cidr: str) -> dict:
    try:
        return _mutate_fw_rule_destinations(rule_name, cidr, add=False)
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True, "message": f"Firewall rule '{rule_name}' not found (nothing to remove)."}
        log.error("remove_cidr_from_firewall_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def restore_firewall_rule(rule_desc: dict, rcg_name: str = None,
                          collection_name: str = None) -> dict:
    """
    Recreate/restore a rule from a stored _describe_fw_rule snapshot — used
    by the change-revert engine to undo deletions and modifications.
    """
    try:
        name = rule_desc["name"]
        kind = rule_desc.get("kind", "network")
        collection = collection_name or rule_desc.get("collection")
        if kind == "application":
            app_protocols = []
            for p in (rule_desc.get("protocols") or []):
                t, _, port = str(p).partition("/")
                app_protocols.append({"protocol_type": t or "Https",
                                      "port": int(port) if port.isdigit() else 443})
            dests, ports, ip_protocols = rule_desc.get("fqdns") or [], None, None
        else:
            dests = rule_desc.get("destinations") or []
            ports = rule_desc.get("ports") or ["*"]
            ip_protocols = rule_desc.get("protocols") or ["Any"]
            app_protocols = None
        # Replace when the rule still exists, else re-add into its collection
        res = replace_firewall_rule(name, kind, rule_desc.get("sources") or ["*"], dests,
                                    ports=ports, ip_protocols=ip_protocols,
                                    app_protocols=app_protocols,
                                    rcg_name=rcg_name, collection_name=collection)
        if res.get("success"):
            return {"success": True, "message": f"Rule '{name}' restored (updated in place)."}
        if kind == "application":
            return add_firewall_application_rule(name, dests, app_protocols,
                                                 source_addresses=rule_desc.get("sources"),
                                                 rcg_name=rcg_name, collection_name=collection)
        return add_firewall_network_rule(name, dests, ports, protocol=ip_protocols,
                                         source_addresses=rule_desc.get("sources"),
                                         rcg_name=rcg_name, collection_name=collection)
    except Exception as exc:
        log.error("restore_firewall_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── 6. Firewall — network rule ────────────────────────────────────────────

def _find_target_collection(rcg, collection_name=None):
    """Filter collection to add into: the named one, or the first filter collection."""
    for rc in (rcg.rule_collections or []):
        if rc.rule_collection_type != "FirewallPolicyFilterRuleCollection":
            continue
        if collection_name is None or rc.name == collection_name:
            return rc
    return None


def _collection_kind_conflict(target, rule_kind: str):
    """
    Azure rejects mixing network and application rules in one collection.
    Returns an error message when the target collection already holds the
    other rule type, else None.
    """
    kinds = {("application" if r.rule_type == "ApplicationRule" else "network")
             for r in (target.rules or [])}
    if kinds and rule_kind not in kinds:
        return (f"Collection '{target.name}' holds {'/'.join(sorted(kinds))} rules — Azure does "
                f"not allow {rule_kind} rules in the same collection. Pick or create a "
                f"{rule_kind}-rule collection instead.")
    return None


@_guard
def add_firewall_network_rule(
    rule_name: str,
    destination_addresses: list,
    destination_ports: list,
    protocol="TCP",                 # str or list of IP protocols
    source_addresses: list = None,
    rcg_name: str = None,
    collection_name: str = None,
) -> dict:
    """Add a network rule to a firewall policy rule collection."""
    try:
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        rcg = client.firewall_policy_rule_collection_groups.get(
            cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME,
            rcg_name or cfg.FIREWALL_RULE_COLLECTION_GROUP)
        target = _find_target_collection(rcg, collection_name)
        if target is None:
            return {"success": False,
                    "message": f"Rule collection '{collection_name or '(any filter)'}' "
                               f"not found in RCG '{rcg.name}'."}
        existing = next((r for r in (target.rules or []) if r.name == rule_name), None)
        if existing is not None:
            return {"success": False, "conflict": True,
                    "existing_rule": _describe_fw_rule(existing, target.name),
                    "message": f"A rule named '{rule_name}' already exists in "
                               f"'{rcg.name}/{target.name}'."}
        conflict = _collection_kind_conflict(target, "network")
        if conflict:
            return {"success": False, "message": conflict}
        protocols = [protocol] if isinstance(protocol, str) else list(protocol or ["Any"])
        new_rule = _build_fw_rule(rule_name, "network", source_addresses,
                                  destination_addresses, ports=destination_ports,
                                  ip_protocols=protocols)
        target.rules = list(target.rules or []) + [new_rule]
        log.info("Adding network rule '%s' to %s/%s", rule_name, rcg.name, target.name)
        client.firewall_policy_rule_collection_groups.begin_create_or_update(
            cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME, rcg.name, rcg,
        ).result()
        return {"success": True,
                "change": {"target": f"fw rule {rule_name} @ {rcg.name}/{target.name}",
                           "before": None, "after": _describe_fw_rule(new_rule, target.name),
                           "revert_op": "remove_fw_rule",
                           "revert_params": {"rule_name": rule_name, "rcg": rcg.name,
                                             "collection": target.name}},
                "message": f"Network rule '{rule_name}' added to {rcg.name}/{target.name}."}
    except Exception as exc:
        log.error("add_firewall_network_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── 7. Firewall — application rule (HTTP/HTTPS only) ─────────────────────

@_guard
def add_firewall_application_rule(
    rule_name: str,
    target_fqdns: list,
    protocols: list,           # list of {"protocol_type": "Https"|"Http", "port": 443}
    source_addresses: list = None,
    rcg_name: str = None,
    collection_name: str = None,
) -> dict:
    """
    Add an application rule (HTTP/HTTPS only) to the firewall policy.
    protocols must only contain Http or Https; target_fqdns must be FQDNs
    (Azure application rules cannot target IP addresses).
    """
    for p in protocols:
        pt = p.get("protocol_type", "")
        if pt not in ("Http", "Https"):
            return {
                "success": False,
                "message": f"Application rules only support Http/Https. '{pt}' is not allowed. Use a Network Rule for other protocols.",
            }
    import ipaddress as _ip
    for f in target_fqdns:
        try:
            _ip.ip_network(str(f).strip(), strict=False)
            return {"success": False,
                    "message": f"'{f}' is an IP address — Azure application rules only accept "
                               f"FQDNs (e.g. *.example.com). Use a Network Rule for IP destinations."}
        except ValueError:
            pass

    try:
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        rcg = client.firewall_policy_rule_collection_groups.get(
            cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME,
            rcg_name or cfg.FIREWALL_RULE_COLLECTION_GROUP)
        target = _find_target_collection(rcg, collection_name)
        if target is None:
            return {"success": False,
                    "message": f"Rule collection '{collection_name or '(any filter)'}' "
                               f"not found in RCG '{rcg.name}'."}
        existing = next((r for r in (target.rules or []) if r.name == rule_name), None)
        if existing is not None:
            return {"success": False, "conflict": True,
                    "existing_rule": _describe_fw_rule(existing, target.name),
                    "message": f"A rule named '{rule_name}' already exists in "
                               f"'{rcg.name}/{target.name}'."}
        conflict = _collection_kind_conflict(target, "application")
        if conflict:
            return {"success": False, "message": conflict}
        new_rule = _build_fw_rule(rule_name, "application", source_addresses,
                                  target_fqdns, app_protocols=protocols)
        target.rules = list(target.rules or []) + [new_rule]
        log.info("Adding application rule '%s' to %s/%s", rule_name, rcg.name, target.name)
        client.firewall_policy_rule_collection_groups.begin_create_or_update(
            cfg.FIREWALL_POLICY_RG, cfg.FIREWALL_POLICY_NAME, rcg.name, rcg,
        ).result()
        return {"success": True,
                "change": {"target": f"fw rule {rule_name} @ {rcg.name}/{target.name}",
                           "before": None, "after": _describe_fw_rule(new_rule, target.name),
                           "revert_op": "remove_fw_rule",
                           "revert_params": {"rule_name": rule_name, "rcg": rcg.name,
                                             "collection": target.name}},
                "message": f"Application rule '{rule_name}' added to {rcg.name}/{target.name} "
                           f"for {target_fqdns}."}
    except Exception as exc:
        log.error("add_firewall_application_rule failed: %s", exc)
        return {"success": False, "message": str(exc)}
