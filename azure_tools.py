"""
Azure SDK helpers — called by the admin agent for hub integration operations.
Credentials come from config.cfg: Service Principal or Managed Identity,
selected by the AZURE_AUTH_MODE setting (editable in /admin/settings).
"""
import functools
import ipaddress
import logging
import re
from config import cfg
from naming import render_name

log = logging.getLogger(__name__)


_GUARD_SECRET_KWARGS = ("password", "secret", "key", "token", "credential")


def _guard(fn):
    """When AZURE_DRY_RUN is on, simulate the call — never touch Azure."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if cfg.AZURE_DRY_RUN:
            # Never echo a secret-shaped kwarg (admin_password, ssh_public_key, ...) into the
            # dry-run log line or the returned message — the message flows straight into the
            # audit trail's `data` column via _audit_azure().
            detail = ", ".join(f"{k}={v}" for k, v in kwargs.items()
                               if v not in (None, "") and isinstance(v, (str, int, bool))
                               and not any(s in k.lower() for s in _GUARD_SECRET_KWARGS))
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


def _tags(extra: dict = None) -> dict:
    """Standard resource tags (owner / env / criticality / creator, …) applied to
    every taggable resource the portal creates. Trims values, drops empties, caps
    Azure's length limits (key ≤512, value ≤256)."""
    out = {}
    for k, v in (extra or {}).items():
        v = str(v if v is not None else "").strip()
        if v and k:
            out[str(k)[:512]] = v[:256]
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
    tags: dict = None,              # owner/env/criticality/creator resource tags
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
        # Build with SDK model objects (NOT a raw snake_case dict) — a plain dict is
        # sent to ARM verbatim and rejected ('Could not find member address_space');
        # models serialize to the camelCase ARM wire format.
        from azure.mgmt.network.models import VirtualNetwork, AddressSpace, Subnet
        vnet_subnets = [Subnet(name=s["name"], address_prefix=s["address_prefix"])
                        for s in subnet_params]
        client.virtual_networks.begin_create_or_update(
            resource_group_name=resource_group,
            virtual_network_name=vnet_name,
            parameters=VirtualNetwork(
                location=location,
                address_space=AddressSpace(address_prefixes=[address_space]),
                subnets=vnet_subnets,
                tags=_tags(tags),
            ),
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
        from azure.mgmt.network.models import Route
        for r in routes:
            params = Route(address_prefix=r["prefix"],
                           next_hop_type=r.get("next_hop_type") or "VirtualAppliance")
            if r.get("next_hop_ip"):
                params.next_hop_ip_address = r["next_hop_ip"]
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

        # SDK model objects (raw snake_case dicts are sent to ARM verbatim and rejected).
        from azure.mgmt.network.models import VirtualNetworkPeering, SubResource

        # Spoke → Hub
        log.info("Creating spoke→hub peering '%s' (%s → %s)", s2h, spoke_vnet_name, cfg.HUB_VNET_NAME)
        spoke_client.virtual_network_peerings.begin_create_or_update(
            resource_group_name=spoke_resource_group,
            virtual_network_name=spoke_vnet_name,
            virtual_network_peering_name=s2h,
            virtual_network_peering_parameters=VirtualNetworkPeering(
                allow_virtual_network_access=allow_vnet_access,
                allow_forwarded_traffic=allow_forwarded_traffic,
                allow_gateway_transit=False,        # spoke never grants transit
                use_remote_gateways=use_remote_gateways,
                remote_virtual_network=SubResource(id=hub_vnet_id),
            ),
        ).result()

        # Hub → Spoke
        log.info("Creating hub→spoke peering '%s' (%s → %s)", h2s, cfg.HUB_VNET_NAME, spoke_vnet_name)
        hub_client.virtual_network_peerings.begin_create_or_update(
            resource_group_name=cfg.HUB_RESOURCE_GROUP,
            virtual_network_name=cfg.HUB_VNET_NAME,
            virtual_network_peering_name=h2s,
            virtual_network_peering_parameters=VirtualNetworkPeering(
                allow_virtual_network_access=allow_vnet_access,
                allow_forwarded_traffic=allow_forwarded_traffic,
                allow_gateway_transit=allow_gateway_transit,
                use_remote_gateways=False,
                remote_virtual_network=SubResource(id=spoke_vnet_id),
            ),
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
    tags: dict = None,              # owner/env/criticality/creator resource tags
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
                "tags": _tags(tags),
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

        from azure.mgmt.network.models import Route
        params = Route(address_prefix=address_prefix, next_hop_type=next_hop_type)
        if next_hop_ip and next_hop_type == "VirtualAppliance":
            params.next_hop_ip_address = next_hop_ip

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

def _hub_vnet_id() -> str:
    return (f"/subscriptions/{cfg.HUB_SUBSCRIPTION_ID}"
            f"/resourceGroups/{cfg.HUB_RESOURCE_GROUP}"
            f"/providers/Microsoft.Network/virtualNetworks/{cfg.HUB_VNET_NAME}")


def check_private_dns_zone(zone_name: str) -> dict:
    """
    Read-only availability check for DNS requests. Two distinct facts:
      exists     — the zone resource is present in the hub's DNS zone RG
      hub_linked — the zone has a virtual-network link to the HUB VNET
                   (this is what "integrated with the hub" means)
    Never mutates anything.
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
        client = _privatedns_client()
        try:
            client.private_zones.get(rg, zone_name)
        except Exception as exc:
            if _is_not_found(exc):
                return {"success": True, "exists": False, "hub_linked": False, "zone": zone_name,
                        "message": f"Zone '{zone_name}' is NOT present in the hub ({rg})."}
            raise
        hub_id = _hub_vnet_id().lower()
        hub_linked = any(
            l.virtual_network and (l.virtual_network.id or "").lower() == hub_id
            for l in client.virtual_network_links.list(rg, zone_name))
        return {"success": True, "exists": True, "hub_linked": hub_linked, "zone": zone_name,
                "message": (f"Zone '{zone_name}' exists and IS linked to the hub VNET."
                            if hub_linked else
                            f"Zone '{zone_name}' exists in {rg} but is NOT linked to the hub VNET.")}
    except Exception as exc:
        log.error("check_private_dns_zone failed: %s", exc)
        return {"success": False, "exists": None, "hub_linked": None,
                "message": f"Could not verify zone availability: {exc}"}


def _privatedns_client(subscription_id: str = None):
    from azure.mgmt.privatedns import PrivateDnsManagementClient
    return PrivateDnsManagementClient(
        _get_credential(),
        subscription_id or cfg.DNS_ZONE_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID)


def _dns_record_dict(rs, record_type: str) -> dict:
    if record_type == "CNAME":
        values = [rs.cname_record.cname] if rs.cname_record and rs.cname_record.cname else []
    else:
        values = [a.ipv4_address for a in (rs.a_records or [])]
    return {"name": rs.name, "type": record_type, "ttl": rs.ttl, "values": values}


def get_dns_record_status(zone: str, record_type: str, record_name: str) -> dict:
    """Read-only: does the zone exist in the hub, and does the record exist —
    with its current value(s)? Never mutates anything."""
    zone = str(zone or "").strip().lower().rstrip(".")
    record_type = (record_type or "A").upper()
    if record_type not in ("A", "CNAME"):
        return {"success": False, "message": "Only A and CNAME records are supported."}
    if not cfg.DNS_ZONE_RG:
        return {"success": False, "message": "Hub private DNS zones resource group not "
                                             "configured (Settings → Hub & Subscriptions)."}
    try:
        client = _privatedns_client()
        try:
            client.private_zones.get(cfg.DNS_ZONE_RG, zone)
        except Exception as exc:
            if _is_not_found(exc):
                return {"success": True, "zone_exists": False, "record_exists": False,
                        "message": f"Zone '{zone}' does NOT exist in the hub ({cfg.DNS_ZONE_RG})."}
            raise
        try:
            rs = client.record_sets.get(cfg.DNS_ZONE_RG, zone, record_type, record_name)
            rec = _dns_record_dict(rs, record_type)
            return {"success": True, "zone_exists": True, "record_exists": True, "record": rec,
                    "message": f"Zone '{zone}' exists; {record_type} record '{record_name}' "
                               f"EXISTS → {', '.join(rec['values']) or '(empty)'} (TTL {rec['ttl']})."}
        except Exception as exc:
            if _is_not_found(exc):
                return {"success": True, "zone_exists": True, "record_exists": False,
                        "message": f"Zone '{zone}' exists; {record_type} record "
                                   f"'{record_name}' does not exist yet — clear to create."}
            raise
    except Exception as exc:
        log.error("get_dns_record_status failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def upsert_dns_record(zone: str, record_type: str, record_name: str, value: str,
                      ttl: int = 3600, on_conflict: str = None) -> dict:
    """
    Create an A/CNAME record in a hub private DNS zone. If a record with the
    same name already exists with a DIFFERENT value, returns a conflict with
    its current definition unless on_conflict='replace' (edit in place).
    """
    zone = str(zone or "").strip().lower().rstrip(".")
    record_type = (record_type or "A").upper()
    value = str(value or "").strip()
    if record_type not in ("A", "CNAME"):
        return {"success": False, "message": "Only A and CNAME records are supported."}
    try:
        client = _privatedns_client()
        before = None
        try:
            rs = client.record_sets.get(cfg.DNS_ZONE_RG, zone, record_type, record_name)
            before = _dns_record_dict(rs, record_type)
        except Exception as exc:
            if not _is_not_found(exc):
                raise
        if before is not None:
            if before["values"] == [value]:
                return {"success": True, "kept_existing": True,
                        "message": f"{record_type} record '{record_name}' already exists with the "
                                   f"requested value ({value}) — nothing to do."}
            if on_conflict != "replace":
                return {"success": False, "conflict": True, "existing_record": before,
                        "message": f"{record_type} record '{record_name}' already exists in "
                                   f"'{zone}' with value(s): {', '.join(before['values'])}."}

        if record_type == "CNAME":
            params = {"ttl": ttl, "cname_record": {"cname": value}}
        else:
            params = {"ttl": ttl, "a_records": [{"ipv4_address": value}]}
        log.info("Upserting %s record '%s' in zone '%s' → %s", record_type, record_name, zone, value)
        client.record_sets.create_or_update(cfg.DNS_ZONE_RG, zone, record_type, record_name, params)
        after = {"name": record_name, "type": record_type, "ttl": ttl, "values": [value]}
        change = {"target": f"DNS {record_type} {record_name}.{zone}",
                  "before": before, "after": after}
        if before:
            change.update({"revert_op": "restore_dns_record",
                           "revert_params": {"zone": zone, "rtype": record_type,
                                             "name": record_name, "values": before["values"],
                                             "ttl": before["ttl"] or 3600}})
        else:
            change.update({"revert_op": "delete_dns_record",
                           "revert_params": {"zone": zone, "rtype": record_type,
                                             "name": record_name}})
        return {"success": True, "replaced_existing": bool(before), "change": change,
                "message": f"{record_type} record '{record_name}.{zone}' "
                           f"{'updated to' if before else 'created →'} {value}."}
    except Exception as exc:
        log.error("upsert_dns_record failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def delete_dns_record(zone: str, record_type: str, record_name: str) -> dict:
    """Delete an A/CNAME record (snapshotted for revert; not-found = success)."""
    try:
        client = _privatedns_client()
        before = None
        try:
            rs = client.record_sets.get(cfg.DNS_ZONE_RG, zone, record_type, record_name)
            before = _dns_record_dict(rs, record_type)
        except Exception as exc:
            if _is_not_found(exc):
                return {"success": True,
                        "message": f"Record '{record_name}' not found in '{zone}' (already removed)."}
            raise
        client.record_sets.delete(cfg.DNS_ZONE_RG, zone, record_type, record_name)
        return {"success": True,
                "change": {"target": f"DNS {record_type} {record_name}.{zone}",
                           "before": before, "after": None,
                           "revert_op": "restore_dns_record",
                           "revert_params": {"zone": zone, "rtype": record_type,
                                             "name": record_name, "values": before["values"],
                                             "ttl": before["ttl"] or 3600}},
                "message": f"{record_type} record '{record_name}.{zone}' deleted."}
    except Exception as exc:
        log.error("delete_dns_record failed: %s", exc)
        return {"success": False, "message": str(exc)}


def get_dns_zone_link_status(zone: str, vnet_name: str = None) -> dict:
    """Read-only: does the zone exist in the hub, and what VNET links does it
    have (optionally: is a specific VNET already linked)?"""
    zone = str(zone or "").strip().lower().rstrip(".")
    if not cfg.DNS_ZONE_RG:
        return {"success": False, "message": "Hub private DNS zones resource group not "
                                             "configured (Settings → Hub & Subscriptions)."}
    try:
        client = _privatedns_client()
        try:
            client.private_zones.get(cfg.DNS_ZONE_RG, zone)
        except Exception as exc:
            if _is_not_found(exc):
                return {"success": True, "zone_exists": False, "links": [], "link_exists": False,
                        "message": f"Zone '{zone}' does NOT exist in the hub ({cfg.DNS_ZONE_RG})."}
            raise
        links = []
        for l in client.virtual_network_links.list(cfg.DNS_ZONE_RG, zone):
            vid = l.virtual_network.id if l.virtual_network else ""
            links.append({"name": l.name, "vnet": vid.split("/")[-1] if vid else "",
                          "registration_enabled": bool(l.registration_enabled),
                          "state": str(l.virtual_network_link_state or "")})
        link_exists = bool(vnet_name) and any(
            l["vnet"].lower() == str(vnet_name).lower() for l in links)
        return {"success": True, "zone_exists": True, "links": links, "link_exists": link_exists,
                "message": (f"Zone '{zone}' exists with {len(links)} VNET link(s)"
                            + (f"; VNET '{vnet_name}' is "
                               + ("ALREADY linked." if link_exists else "not linked yet.")
                               if vnet_name else "."))}
    except Exception as exc:
        log.error("get_dns_zone_link_status failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def create_dns_zone_in_hub(zone: str) -> dict:
    """
    Make a private DNS zone hub-integrated:
      - zone missing            → create it + link to the hub VNET
      - zone exists, not linked → just add the hub VNET link
      - zone already hub-linked → conflict (never silently touched)
    """
    from naming import sanitize
    zone = str(zone or "").strip().lower().rstrip(".")
    if not cfg.DNS_ZONE_RG:
        return {"success": False, "message": "Hub private DNS zones resource group not "
                                             "configured (Settings → Hub & Subscriptions)."}
    try:
        client = _privatedns_client()
        hub_vnet_id = _hub_vnet_id()
        zone_exists = True
        try:
            client.private_zones.get(cfg.DNS_ZONE_RG, zone)
        except Exception as exc:
            if not _is_not_found(exc):
                raise
            zone_exists = False
        if zone_exists:
            links = [{"name": l.name,
                      "vnet": (l.virtual_network.id.split("/")[-1]
                               if l.virtual_network and l.virtual_network.id else ""),
                      "_id": (l.virtual_network.id or "").lower() if l.virtual_network else ""}
                     for l in client.virtual_network_links.list(cfg.DNS_ZONE_RG, zone)]
            if any(l["_id"] == hub_vnet_id.lower() for l in links):
                for l in links:
                    l.pop("_id", None)
                return {"success": False, "conflict": True,
                        "existing_zone": {"name": zone, "links": links},
                        "message": f"Zone '{zone}' is already linked to the hub VNET "
                                   f"({len(links)} link(s) total)."}
        link_name = sanitize(f"link-{cfg.HUB_VNET_NAME}")
        if not zone_exists:
            log.info("Creating private DNS zone '%s' in %s", zone, cfg.DNS_ZONE_RG)
            client.private_zones.begin_create_or_update(
                cfg.DNS_ZONE_RG, zone, {"location": "global"}).result()
        log.info("Linking DNS zone '%s' to hub VNET", zone)
        client.virtual_network_links.begin_create_or_update(
            cfg.DNS_ZONE_RG, zone, link_name,
            {"location": "global", "virtual_network": {"id": hub_vnet_id},
             "registration_enabled": False}).result()
        if zone_exists:
            # Only the link was added — revert must remove just the link.
            return {"success": True,
                    "change": {"target": f"DNS zone {zone} hub link",
                               "before": None,
                               "after": {"zone": zone, "hub_link": link_name},
                               "revert_op": "delete_dns_zone_link",
                               "revert_params": {"zone": zone, "link": link_name}},
                    "message": f"Zone '{zone}' already existed (not hub-linked) — linked it to "
                               f"'{cfg.HUB_VNET_NAME}' (link '{link_name}')."}
        return {"success": True,
                "change": {"target": f"DNS zone {zone} @ hub",
                           "before": None,
                           "after": {"zone": zone, "hub_link": link_name},
                           "revert_op": "delete_dns_zone",
                           "revert_params": {"zone": zone}},
                "message": f"Private DNS zone '{zone}' created in the hub and linked to "
                           f"'{cfg.HUB_VNET_NAME}' (link '{link_name}')."}
    except Exception as exc:
        log.error("create_dns_zone_in_hub failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def link_dns_zone_to_vnet(zone: str, subscription_id: str, resource_group: str,
                          vnet_name: str, on_conflict: str = None) -> dict:
    """Link an existing hub DNS zone to a spoke VNET. Same-VNET link already
    present = satisfied; a colliding link is a conflict (edit after confirm)."""
    from naming import sanitize
    zone = str(zone or "").strip().lower().rstrip(".")
    try:
        client = _privatedns_client()
        try:
            client.private_zones.get(cfg.DNS_ZONE_RG, zone)
        except Exception as exc:
            if _is_not_found(exc):
                return {"success": False,
                        "message": f"Zone '{zone}' does not exist in the hub — it cannot be "
                                   f"linked. Reject the request, or have the requester raise a "
                                   f"'Link my private DNS zone to the Hub' request first."}
            raise
        vnet_id = (f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
                   f"/providers/Microsoft.Network/virtualNetworks/{vnet_name}")
        link_name = sanitize(f"link-{vnet_name}")
        existing = None
        for l in client.virtual_network_links.list(cfg.DNS_ZONE_RG, zone):
            lid = (l.virtual_network.id or "") if l.virtual_network else ""
            if l.name == link_name or lid.lower() == vnet_id.lower():
                existing = {"name": l.name, "vnet": lid.split("/")[-1] if lid else "",
                            "registration_enabled": bool(l.registration_enabled),
                            "state": str(l.virtual_network_link_state or ""),
                            "_same_vnet": lid.lower() == vnet_id.lower()}
                break
        if existing:
            if existing.pop("_same_vnet"):
                return {"success": True, "kept_existing": True,
                        "message": f"Zone '{zone}' is already linked to VNET '{vnet_name}' "
                                   f"(link '{existing['name']}') — nothing to do."}
            if on_conflict != "replace":
                return {"success": False, "conflict": True, "existing_link": existing,
                        "message": f"A link named '{existing['name']}' already exists on "
                                   f"'{zone}' pointing at VNET '{existing['vnet']}'."}
        log.info("Linking DNS zone '%s' to VNET '%s'", zone, vnet_name)
        client.virtual_network_links.begin_create_or_update(
            cfg.DNS_ZONE_RG, zone, link_name,
            {"location": "global", "virtual_network": {"id": vnet_id},
             "registration_enabled": False}).result()
        return {"success": True, "replaced_existing": bool(existing),
                "change": {"target": f"DNS link {link_name} @ {zone}",
                           "before": existing,
                           "after": {"name": link_name, "vnet": vnet_name},
                           "revert_op": "delete_dns_zone_link",
                           "revert_params": {"zone": zone, "link": link_name}},
                "message": f"Zone '{zone}' linked to VNET '{vnet_name}' (link '{link_name}')."}
    except Exception as exc:
        log.error("link_dns_zone_to_vnet failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def delete_dns_zone(zone: str) -> dict:
    """Delete a hub private DNS zone (links removed first; revert engine only)."""
    try:
        client = _privatedns_client()
        try:
            for l in list(client.virtual_network_links.list(cfg.DNS_ZONE_RG, zone)):
                client.virtual_network_links.begin_delete(cfg.DNS_ZONE_RG, zone, l.name).result()
        except Exception as exc:
            if not _is_not_found(exc):
                raise
        client.private_zones.begin_delete(cfg.DNS_ZONE_RG, zone).result()
        return {"success": True, "message": f"Private DNS zone '{zone}' deleted from the hub."}
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True, "message": f"Zone '{zone}' not found (already deleted)."}
        log.error("delete_dns_zone failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def delete_dns_zone_link(zone: str, link_name: str) -> dict:
    """Remove a VNET link from a hub DNS zone (revert engine)."""
    try:
        client = _privatedns_client()
        client.virtual_network_links.begin_delete(cfg.DNS_ZONE_RG, zone, link_name).result()
        return {"success": True, "message": f"Link '{link_name}' removed from zone '{zone}'."}
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True, "message": f"Link '{link_name}' not found (already removed)."}
        log.error("delete_dns_zone_link failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def restore_dns_record(zone: str, record_type: str, record_name: str,
                       values: list, ttl: int = 3600) -> dict:
    """Recreate a record from a stored snapshot (revert engine)."""
    try:
        client = _privatedns_client()
        if record_type == "CNAME":
            params = {"ttl": ttl, "cname_record": {"cname": values[0] if values else ""}}
        else:
            params = {"ttl": ttl, "a_records": [{"ipv4_address": v} for v in values]}
        client.record_sets.create_or_update(cfg.DNS_ZONE_RG, zone, record_type, record_name, params)
        return {"success": True,
                "message": f"{record_type} record '{record_name}.{zone}' restored → "
                           f"{', '.join(values)}."}
    except Exception as exc:
        log.error("restore_dns_record failed: %s", exc)
        return {"success": False, "message": str(exc)}


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


def _match_entries(entries, query):
    """How a rule's address list relates to the queried IP/CIDR. Returns the
    strongest of: exact | covered (a LARGER rule subnet contains the query) |
    any ('*') | partial (overlap / rule covers only part of the query), or None."""
    import ipaddress
    rank = {"partial": 1, "any": 2, "covered": 3, "exact": 4}
    best = None

    def _better(cand):
        return cand and (best is None or rank[cand["type"]] > rank[best["type"]])

    for raw in (entries or []):
        e = str(raw).strip()
        cand = None
        if e in ("*", "0.0.0.0/0"):
            cand = {"type": "any", "entry": e}
        elif "-" in e and "/" not in e:                       # IP range a-b
            try:
                lo, hi = [ipaddress.ip_address(x.strip()) for x in e.split("-", 1)]
                q_lo, q_hi = query.network_address, query.broadcast_address
                if lo <= q_lo and q_hi <= hi:
                    cand = {"type": "covered", "entry": e}
                elif not (q_hi < lo or hi < q_lo):
                    cand = {"type": "partial", "entry": e}
            except (ValueError, TypeError):
                cand = None
        else:
            try:
                net = ipaddress.ip_network(e, strict=False)
                if net.version == query.version:
                    if net == query:
                        cand = {"type": "exact", "entry": e}
                    elif query.subnet_of(net):
                        cand = {"type": "covered", "entry": e}   # larger subnet allowed
                    elif net.subnet_of(query) or net.overlaps(query):
                        cand = {"type": "partial", "entry": e}
            except (ValueError, TypeError):
                cand = None
        if _better(cand):
            best = cand
    return best


def find_firewall_rules_for_address(address: str) -> dict:
    """Find every firewall-policy rule whose source OR destination applies to the
    given IP/CIDR — including when a LARGER subnet in a rule covers it. Read-only,
    runs for real even in dry-run."""
    import ipaddress
    addr = str(address or "").strip()
    if not addr:
        return {"success": False, "message": "Enter an IP address or CIDR (e.g. 10.1.2.3 or 10.1.0.0/16)."}
    try:
        query = ipaddress.ip_network(addr, strict=False)
    except ValueError:
        return {"success": False, "message": f"'{addr}' is not a valid IP address or CIDR."}
    if not (cfg.FIREWALL_POLICY_NAME and cfg.FIREWALL_POLICY_RG):
        return {"success": False, "message": "Firewall policy is not configured (Settings → Firewall)."}
    try:
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        matches = []
        for rcg in _iter_rcgs(client):
            for rc in (rcg.rule_collections or []):
                if rc.rule_collection_type != "FirewallPolicyFilterRuleCollection":
                    continue
                action = getattr(getattr(rc, "action", None), "type", None) or ""
                for r in (rc.rules or []):
                    src_m = _match_entries(r.source_addresses, query)
                    dst_m = None
                    if r.rule_type != "ApplicationRule":
                        dst_m = _match_entries(r.destination_addresses, query)
                    if src_m or dst_m:
                        d = _describe_fw_rule(r, rc.name)
                        d.update({"rcg": rcg.name, "action": action,
                                  "match_source": src_m, "match_destination": dst_m,
                                  "priority": getattr(rc, "priority", None)})
                        matches.append(d)
        order = {"exact": 0, "covered": 1, "any": 2, "partial": 3}
        matches.sort(key=lambda m: min(order.get((m.get("match_source") or {}).get("type"), 9),
                                       order.get((m.get("match_destination") or {}).get("type"), 9)))
        return {"success": True, "address": addr, "matches": matches,
                "message": (f"{len(matches)} firewall rule(s) currently apply to {addr}."
                            if matches else f"No firewall rules currently apply to {addr}.")}
    except Exception as exc:
        log.error("find_firewall_rules_for_address failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


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


# ── AKS cluster lifecycle: status / create (non-blocking) / delete ─────────
# AKS provisioning takes several minutes, so create is fire-and-forget: we send
# the initial PUT (surfacing any validation error immediately) but do NOT wait
# for it to finish, otherwise the request would exceed the gunicorn timeout.
# The admin polls get_aks_cluster_status() to watch provisioningState.

import re as _re


def _containerservice_client(subscription_id: str):
    from azure.mgmt.containerservice import ContainerServiceClient
    return ContainerServiceClient(_get_credential(), subscription_id)


def _aks_dns_prefix(name: str) -> str:
    """A valid dnsPrefix: alphanumerics/hyphens, start+end alphanumeric, ≤ 54 chars."""
    s = _re.sub(r"[^A-Za-z0-9-]", "-", (name or "aks")).strip("-")[:54].strip("-")
    if not s or not s[0].isalnum():
        s = "aks" + s
    return s[:54].rstrip("-") or "akscluster"


def _aks_pool_summary(profiles) -> list:
    out = []
    for p in (profiles or []):
        scale = (f"auto {p.min_count}–{p.max_count}" if getattr(p, "enable_auto_scaling", False)
                 else f"{p.count} node(s)")
        out.append({"name": p.name, "vm_size": p.vm_size, "mode": p.mode,
                    "count": p.count, "scale": scale,
                    "k8s": getattr(p, "orchestrator_version", None)})
    return out


def get_aks_cluster_status(subscription_id: str, resource_group: str,
                           cluster_name: str) -> dict:
    """Read-only: does the cluster exist and what is its provisioningState?
    Runs for real even in dry-run — it never mutates anything."""
    try:
        client = _containerservice_client(subscription_id)
        try:
            mc = client.managed_clusters.get(resource_group, cluster_name)
        except Exception as exc:
            if _is_not_found(exc):
                return {"success": True, "exists": False,
                        "message": f"No AKS cluster '{cluster_name}' in {resource_group} yet — "
                                   f"ready to deploy."}
            raise
        state = mc.provisioning_state or "Unknown"
        power = getattr(getattr(mc, "power_state", None), "code", None)
        pools = _aks_pool_summary(mc.agent_pool_profiles)
        endpoint = mc.private_fqdn or mc.fqdn or "—"
        pool_desc = "; ".join(f"{p['name']} ({p['vm_size']}, {p['scale']})" for p in pools)
        return {"success": True, "exists": True, "provisioning_state": state,
                "power_state": power, "kubernetes_version": mc.kubernetes_version,
                "fqdn": endpoint, "node_pools": pools,
                "message": (f"Cluster '{cluster_name}': {state}"
                            + (f" / {power}" if power else "")
                            + f" · Kubernetes {mc.kubernetes_version} · endpoint {endpoint} · "
                            + (pool_desc or "no node pools"))}
    except Exception as exc:
        log.error("get_aks_cluster_status failed: %s", exc)
        return {"success": False, "exists": None, "message": str(exc)}


@_guard
def _kv_name(base: str) -> str:
    """A globally-unique-ish, VALID Key Vault name. Azure rules: 3-24 chars,
    alphanumeric + hyphen, must begin with a letter, end with a letter/digit, and
    NOT contain consecutive hyphens."""
    import hashlib
    h = hashlib.md5((base or "").encode()).hexdigest()[:6]     # 6 hex chars (alnum) for uniqueness
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", base or "aks")       # runs of non-alnum → single hyphen
    clean = re.sub(r"-+", "-", clean).strip("-")               # collapse + trim hyphens
    if not clean or not clean[0].isalpha():                    # must begin with a letter
        clean = ("kv-" + clean).strip("-")
    prefix = clean[:24 - 1 - len(h)].rstrip("-")               # leave room for "-<hash>", no trailing hyphen
    name = re.sub(r"-+", "-", f"{prefix}-{h}").strip("-")[:24]
    return name if len(name) >= 3 else (name + "kv" + h)[:24]


@_guard
def create_aks_disk_encryption(subscription_id: str, resource_group: str, location: str,
                               base_name: str, tags: dict = None) -> dict:
    """Full customer-managed-key setup for AKS host disk encryption: a Key Vault
    (soft-delete + purge protection — required), an RSA key, and a Disk Encryption
    Set with a system-assigned identity granted wrap/unwrap on the key. Returns
    {success, des_id, key_vault, vault_uri, message}.

    Requires azure-mgmt-keyvault + azure-keyvault-keys. The automation SP needs, on
    the target RG/subscription: Key Vault Contributor (create the vault) and key
    permissions — set AZURE_SP_OBJECT_ID so the vault grants the SP a create/wrap
    access policy; otherwise create the key manually."""
    try:
        from azure.mgmt.keyvault import KeyVaultManagementClient
        from azure.mgmt.keyvault.models import (
            VaultCreateOrUpdateParameters, VaultProperties, Sku as KvSku, SkuName,
            AccessPolicyEntry, Permissions, KeyPermissions)
        from azure.keyvault.keys import KeyClient
        from azure.mgmt.compute import ComputeManagementClient
        from azure.mgmt.compute.models import (
            DiskEncryptionSet, KeyForDiskEncryptionSet, SourceVault, EncryptionSetIdentity)
    except ImportError as exc:
        return {"success": False,
                "message": f"Key Vault SDKs not installed ({exc}). Add azure-mgmt-keyvault "
                           f"and azure-keyvault-keys to requirements and redeploy."}

    tenant = cfg.AZURE_TENANT_ID
    if not tenant:
        return {"success": False, "message": "AZURE_TENANT_ID is required for CMK disk encryption."}
    cred = _get_credential()
    rg_res = ensure_resource_group(subscription_id, resource_group, location)
    if not rg_res.get("success"):
        return {"success": False, "message": f"Resource group: {rg_res.get('message')}"}

    kv_name = _kv_name(base_name)
    des_name = (re.sub(r"-+", "-", re.sub(r"[^a-zA-Z0-9]+", "-", base_name or "aks")).strip("-")[:76]
                or "aks").rstrip("-") + "-des"
    kvm = KeyVaultManagementClient(cred, subscription_id)
    compute = ComputeManagementClient(cred, subscription_id)

    # Access policy for the automation SP so it can create/wrap the key.
    base_policies = []
    if cfg.AZURE_SP_OBJECT_ID:
        base_policies.append(AccessPolicyEntry(
            tenant_id=tenant, object_id=cfg.AZURE_SP_OBJECT_ID,
            permissions=Permissions(keys=[KeyPermissions.GET, KeyPermissions.CREATE,
                                          KeyPermissions.LIST, KeyPermissions.WRAP_KEY,
                                          KeyPermissions.UNWRAP_KEY, KeyPermissions.IMPORT_ENUM])))

    def _vault_props(policies):
        return VaultProperties(
            tenant_id=tenant, sku=KvSku(name=SkuName.STANDARD, family="A"),
            access_policies=policies, enable_soft_delete=True,
            soft_delete_retention_in_days=90, enable_purge_protection=True)

    log.info("CMK: creating Key Vault '%s' in %s/%s", kv_name, resource_group, location)
    vault = kvm.vaults.begin_create_or_update(
        resource_group, kv_name,
        VaultCreateOrUpdateParameters(location=location, properties=_vault_props(base_policies),
                                      tags=_tags(tags))).result()
    vault_uri = vault.properties.vault_uri

    # Create the RSA key (data plane).
    try:
        kc = KeyClient(vault_url=vault_uri, credential=cred)
        key = kc.create_rsa_key(f"{kv_name}-cmk", size=2048)
        key_url = key.id
    except Exception as exc:
        return {"success": False, "message": f"Key Vault '{kv_name}' created, but creating the "
                f"key failed ({str(exc)[:160]}). Grant the SP key-create permission "
                f"(set AZURE_SP_OBJECT_ID) or create the key manually."}

    # Disk Encryption Set with a system-assigned identity.
    log.info("CMK: creating Disk Encryption Set '%s'", des_name)
    des = compute.disk_encryption_sets.begin_create_or_update(
        resource_group, des_name,
        DiskEncryptionSet(location=location, identity=EncryptionSetIdentity(type="SystemAssigned"),
                          active_key=KeyForDiskEncryptionSet(
                              source_vault=SourceVault(id=vault.id), key_url=key_url),
                          tags=_tags(tags))).result()
    des_principal = des.identity.principal_id

    # Grant the DES identity wrap/unwrap/get on the vault key.
    grant = base_policies + [AccessPolicyEntry(
        tenant_id=tenant, object_id=des_principal,
        permissions=Permissions(keys=[KeyPermissions.GET, KeyPermissions.WRAP_KEY,
                                       KeyPermissions.UNWRAP_KEY]))]
    kvm.vaults.begin_create_or_update(
        resource_group, kv_name,
        VaultCreateOrUpdateParameters(location=location, properties=_vault_props(grant),
                                      tags=_tags(tags))).result()

    return {"success": True, "des_id": des.id, "key_vault": kv_name, "vault_uri": vault_uri,
            "message": f"Key Vault '{kv_name}', CMK key and Disk Encryption Set '{des_name}' created "
                       f"for host disk encryption."}


def create_aks_cluster(
    subscription_id: str,
    resource_group: str,
    cluster_name: str,
    location: str,
    subnet_id: str,
    kubernetes_version: str,
    node_pool_name: str,
    node_size: str,
    autoscaling: bool = False,
    node_count: int = 2,
    min_count: int = 2,
    max_count: int = 5,
    tier: str = "Free",               # control-plane SKU tier: Free / Standard / Premium
    zones: str = "default",           # node-pool availability zones: "default" / "1" / "1,2,3"
    on_conflict: str = None,          # "replace" = update an existing cluster after confirmation
    tags: dict = None,                # owner/env/criticality/creator resource tags
    node_admin_username: str = None,  # node-pool Linux local admin username
    node_ssh_key: str = None,         # SSH public key (required by Azure when a username is set)
    os_sku: str = None,               # node image: "Ubuntu" / "AzureLinux"
    os_disk_size_gb: int = None,      # node OS disk size (GB)
    disk_encryption_set_id: str = None,  # CMK Disk Encryption Set (host encryption)
    enable_encryption_at_host: bool = False,
) -> dict:
    """Kick off AKS cluster creation (does NOT wait for provisioning to finish).
    Network profile, security and upgrade settings come from config defaults."""
    try:
        client = _containerservice_client(subscription_id)

        # No silent overwrite: surface an existing cluster's state first.
        before = None
        try:
            existing = client.managed_clusters.get(resource_group, cluster_name)
            before = {"name": existing.name, "location": existing.location,
                      "kubernetes_version": existing.kubernetes_version,
                      "provisioning_state": existing.provisioning_state,
                      "node_pools": _aks_pool_summary(existing.agent_pool_profiles)}
            if on_conflict != "replace":
                return {"success": False, "conflict": True, "existing_cluster": before,
                        "message": f"AKS cluster '{cluster_name}' already exists in {resource_group} "
                                   f"(Kubernetes {existing.kubernetes_version}, "
                                   f"{existing.provisioning_state}) — deploying would UPDATE it in place."}
        except Exception as exc:
            if not _is_not_found(exc):
                raise

        rg_res = ensure_resource_group(subscription_id, resource_group, location)
        if not rg_res.get("success"):
            return {"success": False, "message": f"Resource group: {rg_res.get('message')}"}

        # Build the request with the SDK model classes — passing a raw dict sends
        # snake_case keys on the wire (e.g. 'dns_prefix'), which Azure rejects.
        from azure.mgmt.containerservice.models import (
            ManagedCluster, ManagedClusterAgentPoolProfile, ContainerServiceNetworkProfile,
            ManagedClusterAPIServerAccessProfile, ManagedClusterAADProfile,
            ManagedClusterAutoUpgradeProfile, ManagedClusterIdentity, ManagedClusterSKU)

        pool_name = (node_pool_name or "nodepool1")[:12]
        pool = ManagedClusterAgentPoolProfile(
            name=pool_name, mode="System", vm_size=node_size, os_type="Linux",
            type="VirtualMachineScaleSets", vnet_subnet_id=subnet_id,
            orchestrator_version=kubernetes_version, enable_auto_scaling=bool(autoscaling))
        if autoscaling:
            pool.count, pool.min_count, pool.max_count = int(min_count), int(min_count), int(max_count)
        else:
            pool.count = int(node_count)
        if zones and str(zones).lower() != "default":
            pool.availability_zones = [z.strip() for z in str(zones).split(",") if z.strip()]
        # Node-pool image / OS disk / host encryption
        if os_sku:
            pool.os_sku = os_sku                      # "Ubuntu" | "AzureLinux"
        if os_disk_size_gb:
            pool.os_disk_size_gb = int(os_disk_size_gb)
        if enable_encryption_at_host:
            pool.enable_encryption_at_host = True

        net = ContainerServiceNetworkProfile(
            network_plugin=cfg.AKS_NETWORK_PLUGIN or "azure",
            network_policy=(cfg.AKS_NETWORK_POLICY if cfg.AKS_NETWORK_POLICY not in ("", "none") else None),
            pod_cidr=cfg.AKS_POD_CIDR or None, service_cidr=cfg.AKS_SERVICE_CIDR or None,
            dns_service_ip=cfg.AKS_DNS_SERVICE_IP or None,
            outbound_type=cfg.AKS_OUTBOUND_TYPE or "loadBalancer",
            load_balancer_sku=cfg.AKS_LB_SKU or "standard")
        if cfg.AKS_NETWORK_PLUGIN_MODE:
            net.network_plugin_mode = cfg.AKS_NETWORK_PLUGIN_MODE

        mc = ManagedCluster(
            location=location, identity=ManagedClusterIdentity(type="SystemAssigned"),
            dns_prefix=_aks_dns_prefix(cluster_name), kubernetes_version=kubernetes_version,
            agent_pool_profiles=[pool], network_profile=net,
            api_server_access_profile=ManagedClusterAPIServerAccessProfile(
                enable_private_cluster=bool(cfg.AKS_PRIVATE_CLUSTER)),
            disable_local_accounts=bool(cfg.AKS_DISABLE_LOCAL_ACCOUNTS),
            auto_upgrade_profile=ManagedClusterAutoUpgradeProfile(
                upgrade_channel=(cfg.AKS_UPGRADE_CHANNEL if cfg.AKS_UPGRADE_CHANNEL not in ("", "none") else None),
                node_os_upgrade_channel=cfg.AKS_NODE_OS_UPGRADE_CHANNEL or None))
        if cfg.AKS_ENABLE_AAD:
            mc.aad_profile = ManagedClusterAADProfile(
                managed=True, enable_azure_rbac=bool(cfg.AKS_ENABLE_AZURE_RBAC))
        if tier in ("Free", "Standard", "Premium"):
            mc.sku = ManagedClusterSKU(name="Base", tier=tier)

        # Resource tags (owner/env/criticality/creator)
        mc.tags = _tags(tags)

        # Node-pool Linux local admin (Azure requires an SSH public key with it)
        if node_admin_username:
            from azure.mgmt.containerservice.models import (
                ContainerServiceLinuxProfile, ContainerServiceSshConfiguration,
                ContainerServiceSshPublicKey)
            if not (node_ssh_key or "").strip():
                return {"success": False,
                        "message": "A node-pool admin username needs an SSH public key "
                                   "(Azure requires one for a custom Linux profile)."}
            mc.linux_profile = ContainerServiceLinuxProfile(
                admin_username=node_admin_username,
                ssh=ContainerServiceSshConfiguration(
                    public_keys=[ContainerServiceSshPublicKey(key_data=node_ssh_key.strip())]))

        # Customer-managed key host disk encryption (Disk Encryption Set)
        if disk_encryption_set_id:
            mc.disk_encryption_set_id = disk_encryption_set_id

        log.info("Kicking off AKS '%s' (%s, %s) in %s/%s",
                 cluster_name, kubernetes_version, node_size, resource_group, location)
        # begin_* sends the initial PUT (raising on invalid params) and returns a
        # poller we deliberately drop — provisioning continues server-side.
        client.managed_clusters.begin_create_or_update(resource_group, cluster_name, mc)

        scale_desc = (f"autoscale {min_count}–{max_count}" if autoscaling
                      else f"{node_count} node(s)")
        msg = (f"AKS cluster '{cluster_name}' provisioning started "
               f"(Kubernetes {kubernetes_version}, {tier} tier, pool '{pool_name}' {node_size} · {scale_desc}). "
               f"This takes several minutes — use 'Check Cluster State' to watch progress.")
        if rg_res.get("created"):
            msg = f"RG '{resource_group}' created. " + msg
        after = {"name": cluster_name, "location": location,
                 "kubernetes_version": kubernetes_version, "node_pools": [pool_name]}
        change = {"target": f"AKS {cluster_name} @ {resource_group}",
                  "before": before, "after": after}
        if not before:      # only a brand-new cluster has a clean revert (delete it)
            change.update({"revert_op": "delete_aks_cluster",
                           "revert_params": {"sub": subscription_id, "rg": resource_group,
                                             "cluster": cluster_name}})
        return {"success": True, "replaced_existing": bool(before), "change": change,
                "async": True, "message": msg}
    except Exception as exc:
        log.error("create_aks_cluster failed: %s", exc)
        return {"success": False, "message": str(exc)}


@_guard
def delete_aks_cluster(subscription_id: str, resource_group: str,
                       cluster_name: str) -> dict:
    """Delete an AKS cluster (revert op for a portal-created cluster)."""
    try:
        client = _containerservice_client(subscription_id)
        client.managed_clusters.begin_delete(resource_group, cluster_name).result()
        return {"success": True,
                "message": f"AKS cluster '{cluster_name}' deleted from {resource_group}."}
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True,
                    "message": f"AKS cluster '{cluster_name}' already absent."}
        log.error("delete_aks_cluster failed: %s", exc)
        return {"success": False, "message": str(exc)}


# ── Live option lookups (read-only — run for real even in dry-run) ──────────
# Let the requester/admin pull current choices straight from Azure instead of
# typing them: AKS versions & node sizes, and the VNets/subnets in a scope.

def _compute_client(subscription_id: str):
    from azure.mgmt.compute import ComputeManagementClient
    return ComputeManagementClient(_get_credential(), subscription_id)


def aks_tiers() -> list:
    """The control-plane SKU tiers AKS offers (fixed set, cheapest first)."""
    return ["Free", "Standard", "Premium"]


def _ver_key(v: str):
    parts = []
    for p in str(v).split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return parts


def list_aks_os_skus() -> dict:
    """Node-pool OS images (OS SKUs) the installed SDK supports — Linux only, since
    the node pool is Linux. Azure exposes no per-region list for these, so the
    containerservice SDK's OSSKU enum is the live source (updates on SDK upgrade)."""
    try:
        from azure.mgmt.containerservice.models import OSSKU
        skus = [v.value for v in OSSKU if not str(v.value).lower().startswith("windows")]
        pref = ["Ubuntu", "AzureLinux"]     # common defaults first
        skus.sort(key=lambda s: (pref.index(s) if s in pref else len(pref), s))
        return {"success": True, "os_skus": skus}
    except Exception as exc:
        return {"success": False, "message": str(exc)[:200], "os_skus": ["Ubuntu", "AzureLinux"]}


def list_aks_versions(subscription_id: str, location: str) -> dict:
    """Kubernetes versions available for AKS in a region (newest first)."""
    if not subscription_id or not location:
        return {"success": False, "message": "Subscription ID and region are required."}
    try:
        client = _containerservice_client(subscription_id)
        res = client.managed_clusters.list_kubernetes_versions(location)
        # dict-model: '.values' collides with dict.values(); use item access.
        values = res.get("values") if hasattr(res, "get") else []
        versions = set()
        for item in (values or []):
            patches = (item.get("patchVersions") if hasattr(item, "get") else None) or {}
            if patches:
                versions.update(patches.keys())
            else:
                ver = item.get("version") if hasattr(item, "get") else None
                if ver:
                    versions.add(ver)
        ordered = sorted(versions, key=_ver_key, reverse=True)
        return {"success": True, "versions": ordered}
    except Exception as exc:
        log.error("list_aks_versions failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


_AKS_SIZE_RE = re.compile(r"^Standard_(B|D|DS|DC|E|ES|F|FS)\d", re.I)


def list_vm_sizes(subscription_id: str, location: str) -> dict:
    """AKS-suitable VM sizes available in a region (≥ 2 vCPU, common families)."""
    if not subscription_id or not location:
        return {"success": False, "message": "Subscription ID and region are required."}
    try:
        client = _compute_client(subscription_id)
        sizes = []
        for s in client.virtual_machine_sizes.list(location):
            cores = s.number_of_cores or 0
            if cores < 2 or not _AKS_SIZE_RE.match(s.name or ""):
                continue
            sizes.append({"name": s.name, "cores": cores,
                          "memory_gb": round((s.memory_in_mb or 0) / 1024, 1)})
        # Smaller first, then by name; cap so the dropdown stays usable.
        sizes.sort(key=lambda x: (x["cores"], x["name"]))
        return {"success": True, "sizes": sizes[:150], "total": len(sizes)}
    except Exception as exc:
        log.error("list_vm_sizes failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def _rg_from_id(resource_id: str) -> str:
    m = re.search(r"/resourceGroups/([^/]+)/", resource_id or "", re.I)
    return m.group(1) if m else ""


def _sub_from_id(resource_id: str) -> str:
    m = re.search(r"/subscriptions/([^/]+)/", resource_id or "", re.I)
    return m.group(1) if m else ""


@_guard
def link_aks_private_dns_to_hub(subscription_id: str, resource_group: str,
                                cluster_name: str) -> dict:
    """Link a private AKS cluster's API-server private DNS zone to the hub VNet
    so the hub (and its spokes) can resolve the cluster's private endpoint.
    The system-managed zone lives in the cluster's node resource group."""
    try:
        mc = _containerservice_client(subscription_id).managed_clusters.get(
            resource_group, cluster_name)
        apsp = getattr(mc, "api_server_access_profile", None)
        if not (apsp and getattr(apsp, "enable_private_cluster", False)):
            return {"success": False,
                    "message": "This cluster is public — there is no API-server private DNS zone to link."}
        node_rg = getattr(mc, "node_resource_group", "") or ""
        pdz = getattr(apsp, "private_dns_zone", None) or ""

        if pdz and pdz.lower() not in ("system", "none"):
            zone_sub = _sub_from_id(pdz) or subscription_id
            zone_rg = _rg_from_id(pdz)
            zone_name = pdz.rstrip("/").split("/")[-1]
        else:
            zone_sub, zone_rg, zone_name = subscription_id, node_rg, None
            pc = _privatedns_client(zone_sub)
            for z in pc.private_zones.list_by_resource_group(node_rg):
                if "privatelink" in (z.name or "") and "azmk8s.io" in (z.name or ""):
                    zone_name = z.name
                    break
            if not zone_name:
                return {"success": False,
                        "message": f"AKS private DNS zone not found in {node_rg} — has the cluster "
                                   f"finished provisioning? Run 'Check Cluster State' first."}

        pc = _privatedns_client(zone_sub)
        link_name = ("hub-" + cluster_name)[:80]
        try:
            pc.virtual_network_links.get(zone_rg, zone_name, link_name)
            return {"success": True, "kept_existing": True,
                    "message": f"Private DNS zone '{zone_name}' is already linked to the hub "
                               f"(link '{link_name}')."}
        except Exception as exc:
            if not _is_not_found(exc):
                raise
        pc.virtual_network_links.begin_create_or_update(
            zone_rg, zone_name, link_name,
            {"location": "global", "virtual_network": {"id": _hub_vnet_id()},
             "registration_enabled": False}).result()
        return {"success": True,
                "change": {"target": f"privatedns link {zone_name}/{link_name}", "before": None,
                           "after": {"zone": zone_name, "link": link_name, "hub": cfg.HUB_VNET_NAME},
                           "revert_op": "delete_privatedns_link",
                           "revert_params": {"sub": zone_sub, "zone_rg": zone_rg,
                                             "zone": zone_name, "link": link_name}},
                "message": f"Linked AKS private DNS zone '{zone_name}' to the hub VNet "
                           f"'{cfg.HUB_VNET_NAME}' (link '{link_name}')."}
    except Exception as exc:
        log.error("link_aks_private_dns_to_hub failed: %s", exc)
        return {"success": False, "message": str(exc)[:250]}


@_guard
def remove_privatedns_link(subscription_id: str, zone_rg: str, zone_name: str,
                           link_name: str) -> dict:
    """Remove a private DNS zone's VNet link (revert for the AKS→hub link)."""
    try:
        pc = _privatedns_client(subscription_id)
        pc.virtual_network_links.begin_delete(zone_rg, zone_name, link_name).result()
        return {"success": True, "message": f"Removed hub link '{link_name}' from '{zone_name}'."}
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True, "message": f"Link '{link_name}' already absent."}
        log.error("remove_privatedns_link failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def list_vnets(subscription_id: str) -> dict:
    """All VNets visible in a subscription (name, RG, region, address space)."""
    if not subscription_id:
        return {"success": False, "message": "Subscription ID is required."}
    try:
        client = _network_client(subscription_id)
        vnets = []
        for v in client.virtual_networks.list_all():
            vnets.append({
                "name": v.name, "resource_group": _rg_from_id(v.id), "location": v.location,
                "address_space": ", ".join((v.address_space.address_prefixes if v.address_space else []) or [])})
        vnets.sort(key=lambda x: (x["resource_group"].lower(), x["name"].lower()))
        return {"success": True, "vnets": vnets}
    except Exception as exc:
        log.error("list_vnets failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def list_locations(subscription_id: str) -> dict:
    """Azure regions available to a subscription (name + display name). Uses the
    ARM REST API directly — the subscriptions SDK client isn't always packaged."""
    if not subscription_id:
        return {"success": False, "message": "Subscription ID is required."}
    try:
        import requests
        token = _get_credential().get_token("https://management.azure.com/.default").token
        url = (f"https://management.azure.com/subscriptions/{subscription_id}"
               f"/locations?api-version=2022-12-01")
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        resp.raise_for_status()
        locs = []
        for l in resp.json().get("value", []):
            if (l.get("metadata") or {}).get("regionType", "Physical") != "Physical":
                continue
            locs.append({"name": l.get("name"), "display": l.get("displayName") or l.get("name")})
        locs.sort(key=lambda x: x["display"])
        return {"success": True, "locations": locs}
    except Exception as exc:
        log.error("list_locations failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def list_subnets(subscription_id: str, resource_group: str, vnet_name: str) -> dict:
    """Subnets in a VNet (name + address prefix)."""
    if not all([subscription_id, resource_group, vnet_name]):
        return {"success": False, "message": "Subscription, resource group and VNet are required."}
    try:
        client = _network_client(subscription_id)
        subnets = []
        for s in client.subnets.list(resource_group, vnet_name):
            prefix = s.address_prefix or ", ".join(getattr(s, "address_prefixes", None) or [])
            subnets.append({"name": s.name, "address_prefix": prefix,
                            "used": len(s.ip_configurations or []) if hasattr(s, "ip_configurations") else None})
        return {"success": True, "subnets": subnets}
    except Exception as exc:
        log.error("list_subnets failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


# ── VM live option lookups (read-only — run for real even in dry-run) ───────
# Same idea as the AKS lookups above: subscription/region-scoped, no module-
# level caching (three replicas in prod would each hold a different view — see
# CLAUDE.md), fail soft with a clear message so the form degrades to free text.

def list_vm_skus(subscription_id: str, location: str) -> dict:
    """VM SKUs available in a region for THIS subscription — vCPUs, memory, max
    data disks, PremiumIO / accelerated-networking capability, and the zones
    actually available (after subscription/zone restrictions). Excludes SKUs
    Azure reports as fully unavailable to this subscription in this region
    (restriction type 'Location', e.g. reasonCode NotAvailableForSubscription).
    Confirmed against the live resourceSkus API — capability/restriction field
    names below are not guessed."""
    if not subscription_id or not location:
        return {"success": False, "message": "Subscription ID and region are required."}
    try:
        client = _compute_client(subscription_id)
        allow = [f.strip().lower() for f in (cfg.VM_ALLOWED_SKU_FAMILIES or "").split(",") if f.strip()]
        skus = []
        for s in client.resource_skus.list(filter=f"location eq '{location}'"):
            if s.resource_type != "virtualMachines":
                continue
            if location.lower() not in [l.lower() for l in (s.locations or [])]:
                continue
            restrictions = s.restrictions or []
            if any(r.type == "Location" for r in restrictions):
                continue   # fully unavailable to this subscription in this region
            family = s.family or ""
            if allow and not any(family.lower().startswith(a) or s.name.lower().startswith(a) for a in allow):
                continue
            zone_restricted = set()
            for r in restrictions:
                if r.type == "Zone" and r.restriction_info and r.restriction_info.zones:
                    zone_restricted.update(r.restriction_info.zones)
            zones = []
            for li in (s.location_info or []):
                if (li.location or "").lower() == location.lower():
                    zones = sorted(set(li.zones or []) - zone_restricted)
                    break
            caps = {c.name: c.value for c in (s.capabilities or [])}

            def _capint(key):
                try:
                    return int(caps.get(key))
                except (TypeError, ValueError):
                    return None

            skus.append({
                "name": s.name, "family": family,
                "vcpus": _capint("vCPUs"), "memory_gb": _capint("MemoryGB"),
                "max_data_disks": _capint("MaxDataDiskCount"),
                "premium_io": (caps.get("PremiumIO") or "").lower() == "true",
                "accelerated_networking": (caps.get("AcceleratedNetworkingEnabled") or "").lower() == "true",
                "zones": zones,
            })
        skus.sort(key=lambda x: (x["family"], x["vcpus"] or 0, x["name"]))
        return {"success": True, "skus": skus, "total": len(skus)}
    except Exception as exc:
        log.error("list_vm_skus failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def _vm_sku_lookup(subscription_id: str, location: str, sku_name: str) -> dict:
    """One VM SKU's vCPU count + family in a region (used by the quota check —
    a targeted scan of the same resourceSkus list list_vm_skus uses)."""
    client = _compute_client(subscription_id)
    for s in client.resource_skus.list(filter=f"location eq '{location}'"):
        if s.resource_type == "virtualMachines" and s.name == sku_name:
            caps = {c.name: c.value for c in (s.capabilities or [])}
            try:
                vcpus = int(caps.get("vCPUs"))
            except (TypeError, ValueError):
                vcpus = None
            return {"vcpus": vcpus, "family": s.family or ""}
    return None


def list_vm_zones(subscription_id: str, location: str, sku: str) -> dict:
    """Zones actually available for one SKU in a region (subset of list_vm_skus'
    per-SKU zone list — a separate lookup so the zone picker doesn't need the
    full SKU list re-fetched after the requester changes size)."""
    if not all([subscription_id, location, sku]):
        return {"success": False, "message": "Subscription, region and SKU are required."}
    res = list_vm_skus(subscription_id, location)
    if not res.get("success"):
        return res
    for s in res.get("skus", []):
        if s["name"] == sku:
            return {"success": True, "zones": s["zones"]}
    return {"success": False, "message": f"SKU '{sku}' not found (or unavailable) in {location}."}


def resolve_vm_image(subscription_id: str, location: str, image_ref: str) -> dict:
    """Resolve 'publisher:offer:sku' (-> latest version in this region) or an
    explicit 'publisher:offer:sku:version' to a concrete reference + OS family.
    Used both by list_vm_images (curated picks) and the submit-time guard (to
    decide whether Windows computerName rules apply to whatever the requester
    actually typed, curated or not)."""
    parts = (image_ref or "").split(":")
    if len(parts) not in (3, 4) or not all(parts):
        return {"success": False, "message": "Image must be 'publisher:offer:sku' or "
                                              "'publisher:offer:sku:version'."}
    publisher, offer, sku = parts[0], parts[1], parts[2]
    version = parts[3] if len(parts) == 4 else None
    try:
        client = _compute_client(subscription_id)
        if not version:
            versions = list(client.virtual_machine_images.list(location, publisher, offer, sku))
            if not versions:
                return {"success": False,
                        "message": f"No versions found for {publisher}:{offer}:{sku} in {location}."}
            version = max(versions, key=lambda v: _ver_key(v.name)).name
        img = client.virtual_machine_images.get(location, publisher, offer, sku, version)
        # OperatingSystemTypes is a str-subclass enum — str(...) gives the repr
        # ('OperatingSystemTypes.WINDOWS'), not the value. Use .value explicitly.
        os_type = None
        if img.os_disk_image and img.os_disk_image.operating_system:
            os_type = img.os_disk_image.operating_system.value
        return {"success": True, "reference": f"{publisher}:{offer}:{sku}:{version}",
               "publisher": publisher, "offer": offer, "sku": sku, "version": version,
               "os_type": os_type}
    except Exception as exc:
        log.error("resolve_vm_image failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def _marketplace_friendly_name(publisher: str, offer: str, sku: str) -> str:
    """Best-effort human label for an image — DISPLAY ONLY. The value actually
    stored and submitted is always the exact publisher:offer:sku(:version);
    getting this wrong never corrupts a request, it just shows an uglier name."""
    m = re.match(r"ubuntu-(\d\d)_(\d\d)-lts", offer, re.I)
    if m:
        return f"Ubuntu {m.group(1)}.{m.group(2)} LTS"
    if re.match(r"0001-com-ubuntu-server-\w+", offer, re.I):
        vm = re.match(r"(\d\d)_(\d\d)-lts", sku, re.I)
        if vm:
            return f"Ubuntu {vm.group(1)}.{vm.group(2)} LTS"
    if publisher == "MicrosoftWindowsServer":
        m = re.match(r"(\d{4})-datacenter", sku, re.I)
        if m:
            edition = " (Azure Edition)" if "azure-edition" in sku.lower() else ""
            return f"Windows Server {m.group(1)}{edition}"
    if publisher == "RedHat" and offer.upper() == "RHEL":
        m = re.match(r"(\d+)-lvm", sku, re.I)
        if m:
            return f"RHEL {m.group(1)}"
    m = re.match(r"sles-(\d+)-sp(\d+)", offer, re.I)
    if m:
        return f"SUSE Linux Enterprise {m.group(1)} SP{m.group(2)}"
    m = re.match(r"debian-(\d+)$", offer, re.I)
    if m:
        return f"Debian {m.group(1)}"
    return f"{publisher} {offer} {sku}"


def list_vm_images(subscription_id: str, location: str) -> dict:
    """Resolve the curated image list (Settings → VM Defaults) to their latest
    version in this region — the 'Recommended Images' section of the image
    picker, shown immediately without waiting on 'Load Marketplace Images'. An
    explicit 'publisher:offer:sku:version' override in the form field bypasses
    this list entirely and is resolved on its own via resolve_vm_image() at
    submit time."""
    if not subscription_id or not location:
        return {"success": False, "message": "Subscription ID and region are required."}
    curated = [ln.strip() for ln in (cfg.VM_DEFAULT_IMAGES or "").replace(",", "\n").splitlines()
              if ln.strip()]
    images = []
    for entry in curated:
        res = resolve_vm_image(subscription_id, location, entry)
        publisher, offer, sku = (entry.split(":") + ["", "", ""])[:3]
        name = _marketplace_friendly_name(publisher, offer, sku)
        if res.get("success"):
            images.append({"publisher": publisher, "offer": offer, "sku": sku, "name": name,
                           "version": res["version"], "reference": res["reference"],
                           "os_type": res.get("os_type")})
        else:
            images.append({"publisher": publisher, "offer": offer, "sku": sku, "name": name,
                           "version": None, "error": res.get("message")})
    return {"success": True, "images": images}


# Curated (publisher, offer, sku allow-pattern) catalog for the on-demand
# 'Load Marketplace Images' picker — NOT the full Azure Marketplace (that's
# thousands of entries, almost all irrelevant to a VM request); a hand-picked
# set of common OS families, each queried live for its ACTUAL available SKUs
# in the chosen region. A sku allow-pattern (regex) is given for offers whose
# raw SKU list is a messy mix of legacy/daily/regional variants (RHEL,
# WindowsServer) so only the clean, currently-recommended SKUs surface; None
# means "apply the generic noise filter instead" (clean, small SKU lists).
_MARKETPLACE_CATALOG = [
    ("Canonical", "ubuntu-24_04-lts", None),
    ("Canonical", "0001-com-ubuntu-server-jammy", None),
    ("MicrosoftWindowsServer", "WindowsServer", r"^\d{4}-datacenter(-azure-edition)?(-g2)?$"),
    ("RedHat", "RHEL", r"^\d+-lvm(-gen2)?$"),
    ("SUSE", "sles-15-sp5", None),
    ("Debian", "debian-12", None),
    ("Debian", "debian-11", None),
]

_MARKETPLACE_NOISE = re.compile(
    r"arm64|daily|byos|hpc|fips|cvm|confidential|minimal|test|sap|core|smalldisk|"
    r"hotpatch|chost|hardened|basic|preview|edge|backports", re.I)


def list_marketplace_images(subscription_id: str, location: str) -> dict:
    """On-demand ('Load Marketplace Images') browse of a hand-picked set of
    common OS publishers/offers — queries each offer's actual available SKUs
    live for the region, resolves each surviving one to its latest version,
    and derives a friendly display name. Read-only; runs for real even in
    dry-run. Never raises on one offer's failure — that offer is silently
    skipped so a single bad lookup doesn't blank the whole picker (the caller
    decides what 'the whole thing failed' means, e.g. zero images returned)."""
    if not subscription_id or not location:
        return {"success": False, "message": "Subscription ID and region are required."}
    try:
        client = _compute_client(subscription_id)
        images = []
        offer_failures = []
        for publisher, offer, allow_pattern in _MARKETPLACE_CATALOG:
            try:
                skus = [s.name for s in client.virtual_machine_images.list_skus(location, publisher, offer)]
            except Exception as exc:
                log.error("list_marketplace_images: list_skus failed for %s:%s — %s", publisher, offer, exc)
                offer_failures.append(str(exc)[:150])
                continue
            # Prefer gen2 variants so a dedup-by-name keeps the modern SKU.
            skus.sort(key=lambda s: (0 if re.search(r"gen2|-g2$", s, re.I) else 1, s))
            seen_names = set()
            for sku in skus:
                if allow_pattern:
                    if not re.match(allow_pattern, sku, re.I):
                        continue
                elif _MARKETPLACE_NOISE.search(sku):
                    continue
                name = _marketplace_friendly_name(publisher, offer, sku)
                if name in seen_names:
                    continue
                try:
                    versions = list(client.virtual_machine_images.list(location, publisher, offer, sku))
                    if not versions:
                        continue
                    latest = max(versions, key=lambda v: _ver_key(v.name)).name
                except Exception as exc:
                    log.error("list_marketplace_images: version list failed for %s:%s:%s — %s",
                             publisher, offer, sku, exc)
                    continue
                seen_names.add(name)
                images.append({"publisher": publisher, "offer": offer, "sku": sku, "name": name,
                               "version": latest, "reference": f"{publisher}:{offer}:{sku}:{latest}"})
        images.sort(key=lambda i: (i["publisher"], i["name"]))
        if not images and offer_failures:
            # Every catalog entry failed the SAME way (bad subscription, no
            # access, etc.) — a genuine total failure, not "nothing matched
            # the filters." Report it clearly instead of an empty success.
            return {"success": False, "message": offer_failures[0]}
        return {"success": True, "images": images}
    except Exception as exc:
        log.error("list_marketplace_images failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


_WINDOWS_COMPUTERNAME_TOKENS = ("vm", "prs", "aen")


def derive_windows_computer_name(full_resource_name: str, suffix_width: int) -> dict:
    """Windows osProfile.computerName from a resolved VM RESOURCE name (base +
    -NNN suffix) — independent of, and much tighter than, the 64-char resource
    name. Strips the org's fixed convention tokens (vm-/prs/aen — identical on
    every VM, so blind-truncation would keep the useless tokens and risk
    cross-project collisions) before truncating, keeps <project><env> joined
    without hyphens to save characters, and always preserves -NNN. Only
    truncates the leftmost remaining token (the project name, in the standard
    convention) from the right, and only if still over budget.
    e.g. vm-g100-dev-prs-aen-001 -> g100dev-001.
    Returns {name, truncated, error}. Validated against Azure's documented
    computerName rules (not a hand-rolled list): <=15 chars, letters/digits/
    hyphens only, not all-numeric, no trailing hyphen or period."""
    m = re.match(r"^(.*)-(\d{%d})$" % suffix_width, full_resource_name)
    if not m:
        return {"name": None, "truncated": False,
                "error": f"'{full_resource_name}' doesn't end in the expected -{'0' * suffix_width} suffix."}
    stem, suffix = m.group(1), m.group(2)
    tokens = [t for t in stem.split("-") if t and t.lower() not in _WINDOWS_COMPUTERNAME_TOKENS]
    if not tokens:
        tokens = [stem]   # nothing recognizable to strip — fall back to the whole stem
    name = "".join(tokens) + "-" + suffix
    truncated = False
    if len(name) > 15:
        overflow = len(name) - 15
        if len(tokens[0]) <= overflow:
            return {"name": None, "truncated": False,
                    "error": f"Computer name '{name}' is {len(name)} characters — even removing "
                             f"the project token entirely doesn't fit Windows' 15-character limit "
                             f"with this suffix width."}
        tokens[0] = tokens[0][:len(tokens[0]) - overflow]
        name = "".join(tokens) + "-" + suffix
        truncated = True
    if not re.match(r"^[A-Za-z0-9-]+$", name):
        return {"name": None, "truncated": truncated,
                "error": f"Computer name '{name}' contains characters Windows disallows "
                         f"(letters, digits and hyphens only)."}
    if name.replace("-", "").isdigit():
        return {"name": None, "truncated": truncated,
                "error": f"Computer name '{name}' cannot consist entirely of numbers."}
    if name.endswith("-") or name.endswith("."):
        return {"name": None, "truncated": truncated,
                "error": f"Computer name '{name}' cannot end with a hyphen or period."}
    return {"name": name, "truncated": truncated, "error": None}


# ── Multi-VM naming, collision resolution, and the read-only deploy preview ──
# All read-only except the resource-group scan (VM/NIC/disk list — also
# read-only, runs for real even in dry-run). No creates happen anywhere here.

def resolve_vm_names(base: str, count: int, suffix_width: int, start_at: int = 1) -> list:
    """<base>-001, <base>-002 ... zero-padded to suffix_width, starting at
    start_at. ALWAYS suffixed, even when count == 1, so a later request against
    the same base is never ambiguous about which VM is which."""
    return [f"{base}-{str(i).zfill(suffix_width)}" for i in range(start_at, start_at + count)]


def derive_vm_resource_names(vm_name: str) -> dict:
    """NIC/OS-disk names for one resolved VM name (data disk names are numbered
    per-VM separately, since the count varies per VM plan entry)."""
    return {"nic_name": f"nic-{vm_name}", "os_disk_name": f"osdisk-{vm_name}"}


def _extract_taken_indexes(names: list, base: str, suffix_width: int) -> set:
    """Pure matching logic (no Azure calls) — which VM ordinals a list of
    existing resource names (VMs, NICs, disks) already occupies for this base.
    Kept separate from list_existing_vm_indexes() so the matching logic itself
    is testable with synthetic name lists, independent of live Azure access."""
    esc = re.escape(base)
    patterns = [
        re.compile(rf"^{esc}-(\d{{{suffix_width}}})$"),               # vm-<base>-NNN
        re.compile(rf"^nic-{esc}-(\d{{{suffix_width}}})$"),           # nic-<base>-NNN
        re.compile(rf"^osdisk-{esc}-(\d{{{suffix_width}}})$"),        # osdisk-<base>-NNN
        re.compile(rf"^datadisk-{esc}-(\d{{{suffix_width}}})-\d+$"),  # datadisk-<base>-NNN-01..
    ]
    taken = set()
    for name in names:
        for rx in patterns:
            m = rx.match(name)
            if m:
                taken.add(int(m.group(1)))
                break
    return taken


def list_existing_vm_indexes(subscription_id: str, resource_group: str,
                             base: str, suffix_width: int) -> dict:
    """Which VM ordinals for this base are already taken in the target RG —
    scans VM, NIC AND disk names, not just VMs: a previously failed deploy can
    leave an orphan NIC or disk with no VM behind it, and the next create would
    collide on that name alone. Read-only; runs for real even in dry-run."""
    try:
        compute = _compute_client(subscription_id)
        network = _network_client(subscription_id)
        names = []
        names += [v.name for v in compute.virtual_machines.list(resource_group)]
        names += [d.name for d in compute.disks.list_by_resource_group(resource_group)]
        names += [n.name for n in network.network_interfaces.list(resource_group)]
        return {"success": True, "taken": sorted(_extract_taken_indexes(names, base, suffix_width))}
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True, "taken": []}   # RG doesn't exist yet — nothing taken
        log.error("list_existing_vm_indexes failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def assign_vm_zones(zones, indexes: list) -> list:
    """Round-robin zone assignment keyed by each VM's ACTUAL -NNN index, not its
    position within this batch — so a given ordinal always maps to the same
    zone regardless of which request/collision-offset created it (index 1 ->
    first zone, index 2 -> second, wrapping). zones is the parsed list from
    the request: falsy (None/empty) = no zone pinning; one zone = every VM in
    that zone."""
    if not zones:
        return [None] * len(indexes)
    return [zones[(idx - 1) % len(zones)] for idx in indexes]


def build_vm_plan(subscription_id: str, resource_group: str, base: str, count: int,
                  suffix_width: int, zones, os_type: str, data_disk_count: int = 0) -> dict:
    """Resolve the full per-VM deploy plan for a request: names (skipping past
    every index already taken in the target RG — VM, NIC or disk), zones
    (round-robin), and — for Windows only — the derived, truncated, validated
    computer name (Linux uses the resource name as-is; its 64-char budget
    already covers it). No Azure writes. This is exactly what the admin
    preview shows before any create call, and what stage 4 persists as
    details['vm_plan'] with a per-VM status."""
    idx_res = list_existing_vm_indexes(subscription_id, resource_group, base, suffix_width)
    if not idx_res.get("success"):
        return {"success": False,
                "message": "Could not scan the resource group for existing names — "
                           + idx_res.get("message", "")}
    taken = set(idx_res.get("taken") or [])
    names, indexes, i = [], [], 1
    max_index = 10 ** suffix_width - 1
    while len(names) < count:
        if i > max_index:
            return {"success": False,
                    "message": f"Ran out of available -NNN indexes for base '{base}' "
                               f"({suffix_width}-digit suffix) — too many existing resources "
                               f"share this base name."}
        if i not in taken:
            names.append(f"{base}-{str(i).zfill(suffix_width)}")
            indexes.append(i)
        i += 1

    # Zones are keyed to each VM's ACTUAL index (see assign_vm_zones), so the
    # mapping stays stable across collision offsets, not just batch position.
    zone_list = assign_vm_zones(zones, indexes)
    plan = []
    for name, zone in zip(names, zone_list):
        res_names = derive_vm_resource_names(name)
        computer_name = name
        if os_type == "Windows":
            cn = derive_windows_computer_name(name, suffix_width)
            if cn.get("error"):
                return {"success": False, "message": f"{name}: {cn['error']}"}
            computer_name = cn["name"]
        data_disks = [f"datadisk-{name}-{str(j + 1).zfill(2)}" for j in range(data_disk_count)]
        plan.append({"name": name, "computer_name": computer_name, "zone": zone,
                    "nic": res_names["nic_name"], "os_disk": res_names["os_disk_name"],
                    "data_disks": data_disks, "status": "pending"})
    return {"success": True, "plan": plan}


@_guard
def create_vm(
    subscription_id: str,
    resource_group: str,
    vm_name: str,
    location: str,
    subnet_id: str,
    size: str,
    image_reference: str,          # already resolved: "publisher:offer:sku:version"
    os_disk_name: str,
    os_disk_type: str,
    os_disk_size_gb,
    nic_name: str,
    computer_name: str = None,
    data_disks: list = None,       # [{"name", "disk_type", "size_gb"}, ...]
    zone: str = None,
    os_type: str = "Linux",
    auth_mode: str = "ssh_key",
    ssh_public_key: str = None,
    admin_password: str = None,    # used ONLY for this one call — never logged/returned/retained
    admin_username: str = "azureuser",
    accelerated_networking: bool = False,
    tags: dict = None,
) -> dict:
    """RG (ensure) -> NIC (no public IP, ever; no NSG created — the subnet's own
    applies) -> the VM itself, one create_or_update call. The OS disk and every
    data disk are declared BY NAME inside the VM's own storage profile
    (create_option FromImage/Empty) rather than pre-created as standalone Disk
    resources and then Attach-ed: a disk that's merely Attach-ed never goes
    through Azure's guest-OS provisioning agent, so computer name / admin
    creds / SSH key would silently never land — declaring them inline is the
    only way to get both custom names AND correct provisioning. NIC and every
    disk are tagged delete_option=Delete, so deleting the VM (this op's
    revert) cascades to remove all of them without touching the subnet, its
    NSG, or anything else this portal didn't create.
    Windows VMs need admin_password (Azure requirement) — ssh_key-only auth on
    a Windows image is rejected here rather than sent to Azure malformed."""
    if os_type == "Windows" and auth_mode != "password":
        return {"success": False, "message": "Windows VMs require password authentication — "
                                              "this portal doesn't support SSH-key-only auth "
                                              "for Windows images."}
    try:
        rg_res = ensure_resource_group(subscription_id, resource_group, location)
        if not rg_res.get("success"):
            return {"success": False, "message": f"Resource group: {rg_res.get('message')}"}

        compute = _compute_client(subscription_id)

        # No silent overwrite: a VM with this name already existing means a
        # prior partial deploy or a name collision the plan should have caught.
        try:
            existing = compute.virtual_machines.get(resource_group, vm_name)
            return {"success": False, "conflict": True,
                    "message": f"VM '{vm_name}' already exists in {resource_group} "
                               f"({existing.provisioning_state}) — not overwriting."}
        except Exception as exc:
            if not _is_not_found(exc):
                raise

        from azure.mgmt.network.models import NetworkInterface, NetworkInterfaceIPConfiguration
        from azure.mgmt.network.models import Subnet as _NetSubnet
        from azure.mgmt.compute.models import (
            VirtualMachine, HardwareProfile, StorageProfile, ImageReference, OSDisk, DataDisk,
            ManagedDiskParameters, NetworkProfile, NetworkInterfaceReference,
            NetworkInterfaceReferenceProperties, OSProfile, LinuxConfiguration, SshConfiguration,
            SshPublicKey)

        network = _network_client(subscription_id)
        log.info("Creating NIC '%s' for VM '%s' in %s/%s", nic_name, vm_name, resource_group, location)
        nic = network.network_interfaces.begin_create_or_update(
            resource_group, nic_name,
            NetworkInterface(
                location=location,
                ip_configurations=[NetworkInterfaceIPConfiguration(
                    name="ipconfig1", subnet=_NetSubnet(id=subnet_id),
                    private_ip_allocation_method="Dynamic")],   # no public_ip_address — none, ever
                enable_accelerated_networking=bool(accelerated_networking),
                tags=_tags(tags),
            )).result()

        parts = (image_reference or "").split(":")
        if len(parts) != 4:
            return {"success": False,
                    "message": f"Image reference '{image_reference}' must be fully resolved "
                               f"to publisher:offer:sku:version before deploy."}
        img_ref = ImageReference(publisher=parts[0], offer=parts[1], sku=parts[2], version=parts[3])

        os_disk = OSDisk(
            name=os_disk_name, create_option="FromImage",
            managed_disk=ManagedDiskParameters(storage_account_type=os_disk_type),
            disk_size_gb=int(os_disk_size_gb) if os_disk_size_gb else None,
            delete_option="Delete")
        data_disk_objs = [
            DataDisk(lun=i, name=d["name"], create_option="Empty",
                    disk_size_gb=int(d["size_gb"]),
                    managed_disk=ManagedDiskParameters(storage_account_type=d["disk_type"]),
                    delete_option="Delete")
            for i, d in enumerate(data_disks or [])
        ]

        os_profile_kwargs = {"computer_name": (computer_name or vm_name)[:64],
                             "admin_username": admin_username}
        if auth_mode == "ssh_key":
            os_profile_kwargs["linux_configuration"] = LinuxConfiguration(
                disable_password_authentication=True,
                ssh=SshConfiguration(public_keys=[SshPublicKey(
                    path=f"/home/{admin_username}/.ssh/authorized_keys", key_data=ssh_public_key)]))
        else:
            os_profile_kwargs["admin_password"] = admin_password

        vm_params = dict(
            location=location, tags=_tags(tags),
            hardware_profile=HardwareProfile(vm_size=size),
            storage_profile=StorageProfile(image_reference=img_ref, os_disk=os_disk,
                                           data_disks=data_disk_objs or None),
            os_profile=OSProfile(**os_profile_kwargs),
            network_profile=NetworkProfile(network_interfaces=[NetworkInterfaceReference(
                id=nic.id, properties=NetworkInterfaceReferenceProperties(
                    primary=True, delete_option="Delete"))]),
        )
        if zone:
            vm_params["zones"] = [str(zone)]

        log.info("Creating VM '%s' (%s, %s) in %s/%s — auth_mode=%s",
                 vm_name, size, image_reference, resource_group, location, auth_mode)
        compute.virtual_machines.begin_create_or_update(
            resource_group, vm_name, VirtualMachine(**vm_params)).result()

        msg = f"VM '{vm_name}' created ({size}, zone {zone or 'none'})."
        if rg_res.get("created"):
            msg = f"RG '{resource_group}' created. " + msg
        change = {
            "target": f"VM {vm_name} @ {resource_group}",
            "before": None,
            "after": {"name": vm_name, "size": size, "zone": zone, "nic": nic_name,
                     "os_disk": os_disk_name, "data_disks": [d["name"] for d in (data_disks or [])]},
            "revert_op": "delete_vm",
            "revert_params": {"sub": subscription_id, "rg": resource_group, "vm": vm_name},
        }
        vm_id = (f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
                f"/providers/Microsoft.Compute/virtualMachines/{vm_name}")
        return {"success": True, "change": change,
                "resource_ids": {"vm": vm_id, "nic": nic.id}, "message": msg}
    except Exception as exc:
        # admin_password never appears in exception text we construct ourselves, but be
        # defensive about SDK error strings too — Azure error bodies don't echo request
        # payload secrets, only field-validation messages, so this is safe as written.
        log.error("create_vm failed for '%s': %s", vm_name, exc)
        return {"success": False, "message": str(exc)[:300]}


@_guard
def delete_vm(subscription_id: str, resource_group: str, vm_name: str) -> dict:
    """Delete a VM this portal created (revert op). Its NIC and disks were
    tagged delete_option=Delete at creation time, so this single call cascades
    to remove them too — never the subnet, its NSG, or anything else. Same
    idempotent-on-404 pattern as the other revert ops in this module."""
    try:
        client = _compute_client(subscription_id)
        client.virtual_machines.begin_delete(resource_group, vm_name).result()
        return {"success": True,
                "message": f"VM '{vm_name}' (and its NIC/disks) deleted from {resource_group}."}
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True, "message": f"VM '{vm_name}' already absent."}
        log.error("delete_vm failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def list_disk_skus(location: str, vm_sku_caps: dict = None) -> dict:
    """Managed-disk SKUs, filtered to what the chosen VM SKU actually supports.
    No live Azure call needed — the disk-type list itself is fixed; the only
    per-SKU variable is whether Premium options apply, which list_vm_skus
    already told the caller via 'premium_io'. Kept here (not a static JS list)
    so PremiumV2/UltraSSD zone/region caveats have one server-side place to grow."""
    from config import VM_OS_DISK_TYPES, VM_DATA_DISK_TYPES
    premium_io = bool((vm_sku_caps or {}).get("premium_io"))
    os_types = list(VM_OS_DISK_TYPES) if premium_io else [t for t in VM_OS_DISK_TYPES if "Premium" not in t]
    data_types = list(VM_DATA_DISK_TYPES) if premium_io else [t for t in VM_DATA_DISK_TYPES if "Premium" not in t]
    return {"success": True, "os_disk_types": os_types, "data_disk_types": data_types}


def check_vm_quota(subscription_id: str, location: str, sku: str, count) -> dict:
    """Compare regional + SKU-family vCPU quota against vcpus_per_vm * count.
    Read-only; runs for real even in dry-run. Call again immediately before the
    deploy loop (not only at submit) — headroom moves while a request sits."""
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0
    if not all([subscription_id, location, sku]) or count < 1:
        return {"success": False, "message": "Subscription, region, SKU and a count ≥ 1 are required."}
    try:
        info = _vm_sku_lookup(subscription_id, location, sku)
        if not info or not info.get("vcpus"):
            return {"success": False,
                    "message": f"Could not determine vCPU count for SKU '{sku}' in {location} "
                               f"(unknown SKU, or unavailable to this subscription)."}
        vcpus_per_vm = info["vcpus"]
        requested = vcpus_per_vm * count
        client = _compute_client(subscription_id)
        usage_by_name = {u.name.value: u for u in client.usage.list(location)}
        checks = []
        for key, label in (("cores", "Regional vCPUs"),
                           (info["family"], f"{info['family']} vCPUs")):
            u = usage_by_name.get(key)
            if not u:
                continue
            available = int(u.limit) - int(u.current_value)
            checks.append({"name": label, "current": int(u.current_value), "limit": int(u.limit),
                           "available": available, "requested": requested,
                           "fits": available >= requested})
        fits = bool(checks) and all(c["fits"] for c in checks)
        return {"success": True, "fits": fits, "vcpus_per_vm": vcpus_per_vm,
                "requested_vcpus": requested, "checks": checks,
                "message": ("Fits within quota." if fits else
                            "Requested vCPUs exceed available quota — " +
                            "; ".join(f"{c['name']}: requested {c['requested']}, available {c['available']}"
                                     for c in checks if not c["fits"]))}
    except Exception as exc:
        log.error("check_vm_quota failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


# ── Network diagnostics helpers (read-only) ────────────────────────────────
# Used by netdiag.py to trace a source→destination path: locate an IP's VNet/
# subnet, look up the UDR route toward a destination, and find the private DNS
# zone for an FQDN. All read-only; run for real even in dry-run.

def _is_cidr(s) -> bool:
    try:
        ipaddress.ip_network(str(s), strict=False)
        return True
    except ValueError:
        return False


def _diag_subs(*hints) -> list:
    subs = []
    for s in list(hints) + [cfg.HUB_SUBSCRIPTION_ID, cfg.SPOKE_SUBSCRIPTION_ID]:
        s = (s or "").strip()
        if s and s not in subs:
            subs.append(s)
    return subs


def locate_ip(ip: str, *subscription_hints) -> dict:
    """Find the VNet/subnet a (private) IP belongs to, across known subscriptions."""
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return {"found": False, "message": f"'{ip}' is not a valid IP address."}
    for sub in _diag_subs(*subscription_hints):
        try:
            client = _network_client(sub)
            for v in client.virtual_networks.list_all():
                spaces = (v.address_space.address_prefixes if v.address_space else []) or []
                if not any(addr in ipaddress.ip_network(sp, strict=False) for sp in spaces if _is_cidr(sp)):
                    continue
                rg = _rg_from_id(v.id)
                for sn in client.subnets.list(rg, v.name):
                    prefixes = [sn.address_prefix] if sn.address_prefix else \
                        list(getattr(sn, "address_prefixes", None) or [])
                    if any(addr in ipaddress.ip_network(p, strict=False) for p in prefixes if _is_cidr(p)):
                        rt = getattr(sn, "route_table", None)
                        return {"found": True, "subscription": sub, "resource_group": rg,
                                "vnet": v.name, "vnet_id": v.id, "subnet": sn.name,
                                "address_prefix": prefixes[0] if prefixes else "",
                                "route_table_id": rt.id if rt else None,
                                "dns_servers": list((v.dhcp_options.dns_servers
                                                     if v.dhcp_options else []) or [])}
        except Exception as exc:
            log.error("locate_ip in %s failed: %s", sub, exc)
    return {"found": False,
            "message": f"{ip} is not inside any VNet in the hub/spoke subscriptions "
                       f"(it may be external, or in a subscription this tool can't see)."}


def route_lookup(route_table_id: str, dest_ip: str) -> dict:
    """Longest-prefix-match UDR route for dest_ip in a subnet's route table.
    NOTE: only the associated user-defined routes are examined — Azure system
    and BGP/peering routes are not visible here."""
    if not route_table_id:
        return {"has_udr": False,
                "message": "No route table (UDR) attached — Azure system routes apply "
                           "(local VNet, peering, default Internet)."}
    try:
        sub, rg = _sub_from_id(route_table_id), _rg_from_id(route_table_id)
        name = route_table_id.rstrip("/").split("/")[-1]
        rt = _network_client(sub).route_tables.get(rg, name)
        dest = ipaddress.ip_address(str(dest_ip).strip())
        best = None
        routes = []
        for r in (rt.routes or []):
            routes.append({"name": r.name, "prefix": r.address_prefix,
                           "next_hop_type": r.next_hop_type,
                           "next_hop_ip": r.next_hop_ip_address})
            if not _is_cidr(r.address_prefix):
                continue
            net = ipaddress.ip_network(r.address_prefix, strict=False)
            if dest in net and (best is None or net.prefixlen > best[0]):
                best = (net.prefixlen, r)
        match = None
        if best:
            r = best[1]
            match = {"name": r.name, "prefix": r.address_prefix,
                     "next_hop_type": r.next_hop_type, "next_hop_ip": r.next_hop_ip_address}
        return {"has_udr": True, "table": name, "route_count": len(routes),
                "match": match, "routes": routes}
    except Exception as exc:
        log.error("route_lookup failed: %s", exc)
        return {"has_udr": False, "message": str(exc)[:150]}


def private_dns_for_fqdn(fqdn: str, source_vnet_id: str = None) -> dict:
    """Is there a private DNS zone (in the hub DNS RG) covering the FQDN, and is
    it linked to the hub / the source VNet?"""
    fqdn = str(fqdn or "").strip().lower().rstrip(".")
    rg = cfg.DNS_ZONE_RG
    if not rg:
        return {"checked": False, "message": "Hub private DNS zone RG not configured (Settings → Hub)."}
    labels = fqdn.split(".")
    if len(labels) < 2:
        return {"checked": False, "message": "Not an FQDN."}
    try:
        pc = _privatedns_client()
        zone = None
        for i in range(1, len(labels) - 1):
            cand = ".".join(labels[i:])
            try:
                pc.private_zones.get(rg, cand)
                zone = cand
                break
            except Exception as exc:
                if not _is_not_found(exc):
                    raise
        if not zone:
            return {"checked": True, "zone": None,
                    "message": "No matching private DNS zone found in the hub."}
        linked = [(l.virtual_network.id or "").lower()
                  for l in pc.virtual_network_links.list(rg, zone) if l.virtual_network]
        return {"checked": True, "zone": zone,
                "hub_linked": _hub_vnet_id().lower() in linked,
                "source_linked": bool(source_vnet_id and source_vnet_id.lower() in linked),
                "linked_count": len(linked)}
    except Exception as exc:
        log.error("private_dns_for_fqdn failed: %s", exc)
        return {"checked": False, "message": str(exc)[:150]}


def aks_source_subnet(subscription_id: str, resource_group: str, cluster_name: str) -> dict:
    """The VNet/subnet an AKS cluster's (system) node pool sits in."""
    try:
        mc = _containerservice_client(subscription_id).managed_clusters.get(
            resource_group, cluster_name)
        for p in (mc.agent_pool_profiles or []):
            sid = getattr(p, "vnet_subnet_id", None)
            if sid:
                return {"found": True, "subscription": _sub_from_id(sid) or subscription_id,
                        "resource_group": _rg_from_id(sid),
                        "vnet": sid.split("/virtualNetworks/")[-1].split("/")[0],
                        "subnet": sid.rstrip("/").split("/")[-1], "subnet_id": sid}
        return {"found": False, "message": "Cluster has no VNet-integrated node pool (kubenet?)."}
    except Exception as exc:
        log.error("aks_source_subnet failed: %s", exc)
        return {"found": False, "message": str(exc)[:150]}


def subnet_details(subnet_id: str) -> dict:
    """VNet/subnet/route-table/DNS facts for a subnet resource id."""
    try:
        sub, rg = _sub_from_id(subnet_id), _rg_from_id(subnet_id)
        vnet = subnet_id.split("/virtualNetworks/")[-1].split("/")[0]
        sname = subnet_id.rstrip("/").split("/")[-1]
        client = _network_client(sub)
        sn = client.subnets.get(rg, vnet, sname)
        v = client.virtual_networks.get(rg, vnet)
        prefixes = [sn.address_prefix] if sn.address_prefix else \
            list(getattr(sn, "address_prefixes", None) or [])
        rt = getattr(sn, "route_table", None)
        return {"found": True, "subscription": sub, "resource_group": rg, "vnet": vnet,
                "vnet_id": v.id, "subnet": sname,
                "address_prefix": prefixes[0] if prefixes else "",
                "route_table_id": rt.id if rt else None,
                "dns_servers": list((v.dhcp_options.dns_servers if v.dhcp_options else []) or [])}
    except Exception as exc:
        log.error("subnet_details failed: %s", exc)
        return {"found": False, "message": str(exc)[:150]}


def resolve_private_fqdn(fqdn: str) -> dict:
    """Resolve an FQDN through the hub private DNS zones → IP(s). Returns
    {resolved, ip:[...], zone, record, ...} or an explanation why it won't resolve."""
    fqdn = str(fqdn or "").strip().lower().rstrip(".")
    info = private_dns_for_fqdn(fqdn)
    if not info.get("checked"):
        return {"resolved": False, "zone": None, "message": info.get("message", "DNS check unavailable.")}
    zone = info.get("zone")
    if not zone:
        return {"resolved": False, "zone": None,
                "message": "No private DNS zone in the hub covers this name."}
    record_name = fqdn[:-len(zone)].rstrip(".") if fqdn.endswith(zone) else fqdn
    record_name = record_name or "@"
    a = get_dns_record_status(zone, "A", record_name)
    if a.get("success") and a.get("record_exists"):
        return {"resolved": True, "zone": zone, "record": record_name,
                "ip": a["record"]["values"], "hub_linked": info.get("hub_linked"),
                "source_linked": info.get("source_linked")}
    cn = get_dns_record_status(zone, "CNAME", record_name)
    if cn.get("success") and cn.get("record_exists"):
        return {"resolved": False, "zone": zone, "record": record_name,
                "cname": cn["record"]["values"],
                "message": f"'{fqdn}' is a CNAME → {', '.join(cn['record']['values'])}; "
                           f"resolve that target."}
    return {"resolved": False, "zone": zone, "record": record_name,
            "hub_linked": info.get("hub_linked"), "source_linked": info.get("source_linked"),
            "message": f"Private zone '{zone}' exists but has no A record for '{record_name}' "
                       f"— the name will not resolve internally."}


def vnet_peerings(subscription_id: str, resource_group: str, vnet_name: str) -> dict:
    """List a VNet's peerings (name, state, remote VNet)."""
    try:
        client = _network_client(subscription_id)
        out = []
        for p in client.virtual_network_peerings.list(resource_group, vnet_name):
            rid = (p.remote_virtual_network.id if p.remote_virtual_network else "") or ""
            out.append({"name": p.name, "state": str(p.peering_state or ""),
                        "remote_id": rid.lower(), "remote": rid.split("/")[-1] if rid else ""})
        return {"success": True, "peerings": out}
    except Exception as exc:
        log.error("vnet_peerings failed: %s", exc)
        return {"success": False, "message": str(exc)[:150]}


def find_firewall_rules_for_pair(source: str, destination: str) -> dict:
    """Rules where the SOURCE side covers `source` AND the DESTINATION side covers
    `destination` (both, coverage-aware) — i.e. rules that actually apply to the
    src→dst pair, not just any rule containing one address. Destination may be an
    IP/CIDR (network-rule destinations) or an FQDN (application-rule FQDNs)."""
    src = str(source or "").strip()
    dst = str(destination or "").strip()
    if not src or not dst:
        return {"success": False, "message": "Both source and destination are required."}
    try:
        src_q = ipaddress.ip_network(src, strict=False)
    except ValueError:
        return {"success": False, "message": f"Source '{src}' must be an IP or CIDR."}
    dst_is_ip = _is_cidr(dst)
    dst_q = ipaddress.ip_network(dst, strict=False) if dst_is_ip else None
    dst_fqdn = None if dst_is_ip else dst.lower().rstrip(".")
    if not (cfg.FIREWALL_POLICY_NAME and cfg.FIREWALL_POLICY_RG):
        return {"success": False, "message": "Firewall policy is not configured (Settings → Firewall)."}
    try:
        client = _network_client(cfg.HUB_SUBSCRIPTION_ID)
        matches = []
        for rcg in _iter_rcgs(client):
            for rc in (rcg.rule_collections or []):
                if rc.rule_collection_type != "FirewallPolicyFilterRuleCollection":
                    continue
                action = getattr(getattr(rc, "action", None), "type", None) or ""
                for r in (rc.rules or []):
                    src_m = _match_entries(r.source_addresses, src_q)
                    if not src_m:
                        continue
                    dst_m = None
                    if r.rule_type == "ApplicationRule":
                        if dst_fqdn and _fqdn_covers(r.target_fqdns, dst_fqdn):
                            dst_m = {"type": "fqdn", "entry": ", ".join(list(r.target_fqdns or [])[:5])}
                    elif dst_q is not None:
                        dst_m = _match_entries(r.destination_addresses, dst_q)
                    if dst_m:
                        d = _describe_fw_rule(r, rc.name)
                        d.update({"rcg": rcg.name, "action": action,
                                  "match_source": src_m, "match_destination": dst_m})
                        matches.append(d)
        rank = {"exact": 0, "covered": 1, "fqdn": 1, "any": 2, "partial": 3}
        matches.sort(key=lambda m: rank.get((m.get("match_source") or {}).get("type"), 9)
                     + rank.get((m.get("match_destination") or {}).get("type"), 9))
        return {"success": True, "source": src, "destination": dst, "matches": matches,
                "message": (f"{len(matches)} rule(s) apply to {src} → {dst}." if matches
                            else f"No firewall rule matches {src} → {dst} (source and destination together) "
                                 f"— traffic would be denied by default.")}
    except Exception as exc:
        log.error("find_firewall_rules_for_pair failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def list_subscriptions() -> dict:
    """Subscriptions the automation SP can see (ARM REST). Used for the
    subscription inventory when the cost SP isn't configured."""
    try:
        import requests
        token = _get_credential().get_token("https://management.azure.com/.default").token
        resp = requests.get("https://management.azure.com/subscriptions?api-version=2022-12-01",
                            headers={"Authorization": f"Bearer {token}"}, timeout=20)
        resp.raise_for_status()
        subs = [{"id": s.get("subscriptionId"),
                 "name": s.get("displayName") or s.get("subscriptionId"),
                 "state": s.get("state")} for s in resp.json().get("value", [])]
        subs.sort(key=lambda s: s["name"].lower())
        return {"success": True, "subscriptions": subs}
    except Exception as exc:
        log.error("list_subscriptions failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


# ── Storage Account: live option lookups (read-only — same {ok,data,error}-
# feeding {success,...} shape as the VM lookups above) + deploy/revert. ──────

def _storage_client(subscription_id: str):
    from azure.mgmt.storage import StorageManagementClient
    return StorageManagementClient(_get_credential(), subscription_id)


def _msi_client(subscription_id: str):
    from azure.mgmt.msi import ManagedServiceIdentityClient
    return ManagedServiceIdentityClient(_get_credential(), subscription_id)


def list_storage_skus(subscription_id: str, location: str) -> dict:
    """SKU/kind combinations Azure actually offers for storage accounts in this
    subscription/region, via the same live resourceSkus scan list_vm_skus uses
    (Microsoft.Storage/storageAccounts instead of virtualMachines). Restricted
    combinations (not available to this subscription in this region) are
    excluded, same rule as list_vm_skus."""
    if not subscription_id or not location:
        return {"success": False, "message": "Subscription ID and region are required."}
    try:
        client = _storage_client(subscription_id)
        skus = []
        for s in client.skus.list():
            if s.resource_type != "storageAccounts":
                continue
            locs = [l.lower() for l in (s.locations or [])]
            if location.lower() not in locs:
                continue
            restrictions = s.restrictions or []
            if any(r.reason_code == "NotAvailableForSubscription" for r in restrictions):
                continue
            skus.append({"name": s.name, "tier": s.tier, "kind": s.kind})
        skus.sort(key=lambda x: (x["kind"] or "", x["name"]))
        return {"success": True, "skus": skus, "total": len(skus)}
    except Exception as exc:
        log.error("list_storage_skus failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def check_storage_name_availability(subscription_id: str, name: str) -> dict:
    """Azure's real global-uniqueness check for a storage account name — the
    same live re-verification VM's stage-3 SKU/VNet checks do, not a cached
    or client-side guess."""
    if not subscription_id or not name:
        return {"success": False, "message": "Subscription ID and account name are required."}
    try:
        client = _storage_client(subscription_id)
        res = client.storage_accounts.check_name_availability({
            "name": name, "type": "Microsoft.Storage/storageAccounts"})
        return {"success": True, "available": bool(res.name_available),
                "reason": res.reason, "message": res.message}
    except Exception as exc:
        log.error("check_storage_name_availability failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def list_user_assigned_identities(subscription_id: str, resource_group: str = None) -> dict:
    """User-assigned managed identities visible to this subscription (or scoped
    to one resource group, if given) — for the Storage Account identity picker."""
    if not subscription_id:
        return {"success": False, "message": "Subscription ID is required."}
    try:
        client = _msi_client(subscription_id)
        items = (client.user_assigned_identities.list_by_resource_group(resource_group)
                 if resource_group else client.user_assigned_identities.list_by_subscription())
        identities = [{"id": i.id, "name": i.name, "resource_group": _rg_from_id(i.id),
                      "location": i.location, "principal_id": i.principal_id}
                     for i in items]
        identities.sort(key=lambda x: x["name"].lower())
        return {"success": True, "identities": identities, "total": len(identities)}
    except Exception as exc:
        log.error("list_user_assigned_identities failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def list_keyvaults(subscription_id: str) -> dict:
    """Key Vaults visible to this subscription — for the Storage Account CMK
    Key Vault picker. Uses the same KeyVaultManagementClient as
    create_aks_disk_encryption, listing instead of creating."""
    if not subscription_id:
        return {"success": False, "message": "Subscription ID is required."}
    try:
        from azure.mgmt.keyvault import KeyVaultManagementClient
    except ImportError as exc:
        return {"success": False, "message": f"Key Vault SDK not installed ({exc})."}
    try:
        kvm = KeyVaultManagementClient(_get_credential(), subscription_id)
        vaults = [{"name": v.name, "resource_group": _rg_from_id(v.id), "location": v.location}
                 for v in kvm.vaults.list()]
        vaults.sort(key=lambda x: x["name"].lower())
        return {"success": True, "vaults": vaults, "total": len(vaults)}
    except Exception as exc:
        log.error("list_keyvaults failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def list_keyvault_keys(subscription_id: str, vault_name: str) -> dict:
    """Keys in one Key Vault, each with its version list inline — covers both
    the 'Key Picker' and 'Key Version' picker in a single call. Resolves the
    vault's URI via KeyVaultManagementClient (we're only given a name, not a
    resource group), then reads keys via the data-plane KeyClient, same as
    create_aks_disk_encryption."""
    if not subscription_id or not vault_name:
        return {"success": False, "message": "Subscription ID and vault name are required."}
    try:
        from azure.mgmt.keyvault import KeyVaultManagementClient
        from azure.keyvault.keys import KeyClient
    except ImportError as exc:
        return {"success": False, "message": f"Key Vault SDK not installed ({exc})."}
    try:
        cred = _get_credential()
        kvm = KeyVaultManagementClient(cred, subscription_id)
        vault = next((v for v in kvm.vaults.list() if v.name.lower() == vault_name.lower()), None)
        if not vault:
            return {"success": False, "message": f"Key Vault '{vault_name}' not found in this subscription."}
        kc = KeyClient(vault_url=vault.properties.vault_uri, credential=cred)
        keys = []
        for k in kc.list_properties_of_keys():
            if not k.enabled:
                continue
            versions = [v.version for v in kc.list_properties_of_key_versions(k.name) if v.enabled]
            keys.append({"name": k.name, "versions": versions})
        keys.sort(key=lambda x: x["name"].lower())
        return {"success": True, "keys": keys, "total": len(keys)}
    except Exception as exc:
        log.error("list_keyvault_keys failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


@_guard
def create_storage_account(
    subscription_id: str, resource_group: str, account_name: str, location: str,
    kind: str = "StorageV2", sku: str = "Standard_LRS", access_tier: str = "Hot",
    public_network_access: str = "Disabled",
    allowed_ip_rules: list = None, allowed_subnet_ids: list = None,
    identity_type: str = "system", user_assigned_identity_id: str = None,
    cmk_keyvault_uri: str = None, cmk_key_name: str = None, cmk_key_version: str = None,
    blob_versioning: bool = True, change_feed: bool = False, soft_delete: bool = True,
    soft_delete_days: int = 30, containers: list = None, tags: dict = None,
) -> dict:
    """Deploy a Storage Account: resource group -> account (network rules,
    identity, encryption all applied in the single create call, exactly the
    security-default posture below regardless of what else was requested) ->
    blob/file service properties -> containers (skipping any that already
    exist, so a re-deploy after a partial failure doesn't error out).

    Security defaults are HARDCODED here, not settings-editable (see
    config.py's "storage" category note): TLS 1.2, HTTPS-only, shared-key
    access disabled, blob public access disabled, infrastructure encryption
    enabled, network default action Deny. `public_network_access` and the
    allow-lists only ever narrow access from that secure baseline — they
    can't loosen the defaults above.

    Object replication and a private endpoint, if requested, are separate,
    best-effort steps (create_object_replication_policy /
    create_storage_private_endpoint) — call them after this succeeds; a
    failure there doesn't mean the storage account itself failed to deploy.

    Returns {success, message, resource_ids, steps, dry_run}."""
    from azure.mgmt.storage.models import (
        StorageAccountCreateParameters, Sku, NetworkRuleSet, IPRule, VirtualNetworkRule,
        Encryption, EncryptionServices, EncryptionService, KeyVaultProperties,
        Identity, UserAssignedIdentity, BlobServiceProperties, ChangeFeed,
        DeleteRetentionPolicy, RestorePolicyProperties, FileServiceProperties, BlobContainer,
    )

    steps = []
    rg_res = ensure_resource_group(subscription_id, resource_group, location)
    steps.append({"step": "resource_group", "success": rg_res.get("success"), "message": rg_res.get("message")})
    if not rg_res.get("success"):
        return {"success": False, "message": f"Resource group: {rg_res.get('message')}", "steps": steps}

    try:
        client = _storage_client(subscription_id)

        network_rule_set = NetworkRuleSet(
            default_action="Deny",
            ip_rules=[IPRule(ip_address_or_range=ip) for ip in (allowed_ip_rules or [])],
            virtual_network_rules=[VirtualNetworkRule(virtual_network_resource_id=sid)
                                   for sid in (allowed_subnet_ids or [])],
        )

        encryption_kwargs = {
            "services": EncryptionServices(blob=EncryptionService(enabled=True),
                                           file=EncryptionService(enabled=True)),
            "key_source": "Microsoft.Storage",
            "require_infrastructure_encryption": True,
        }
        if cmk_keyvault_uri and cmk_key_name:
            encryption_kwargs["key_source"] = "Microsoft.Keyvault"
            encryption_kwargs["key_vault_properties"] = KeyVaultProperties(
                key_name=cmk_key_name, key_version=cmk_key_version or "", key_vault_uri=cmk_keyvault_uri)
        encryption = Encryption(**encryption_kwargs)

        if identity_type == "user" and user_assigned_identity_id:
            identity = Identity(type="UserAssigned",
                                user_assigned_identities={user_assigned_identity_id: UserAssignedIdentity()})
        else:
            identity = Identity(type="SystemAssigned")

        params = StorageAccountCreateParameters(
            sku=Sku(name=sku), kind=kind, location=location, tags=_tags(tags), identity=identity,
            access_tier=access_tier, public_network_access=public_network_access,
            network_rule_set=network_rule_set, encryption=encryption,
            enable_https_traffic_only=True, minimum_tls_version="TLS1_2",
            allow_shared_key_access=False, allow_blob_public_access=False,
        )
        account = client.storage_accounts.begin_create(resource_group, account_name, params).result()
        steps.append({"step": "storage_account", "success": True, "message": f"Storage account '{account_name}' created."})
    except Exception as exc:
        log.error("create_storage_account failed: %s", exc)
        steps.append({"step": "storage_account", "success": False, "message": str(exc)[:300]})
        return {"success": False, "message": str(exc)[:300], "steps": steps}

    try:
        client.blob_services.set_service_properties(resource_group, account_name, BlobServiceProperties(
            is_versioning_enabled=bool(blob_versioning),
            change_feed=ChangeFeed(enabled=bool(change_feed)),
            delete_retention_policy=DeleteRetentionPolicy(enabled=bool(soft_delete), days=int(soft_delete_days or 30)),
            restore_policy=RestorePolicyProperties(enabled=False),
        ))
        steps.append({"step": "blob_service_properties", "success": True,
                     "message": "Blob versioning/change-feed/soft-delete applied."})
    except Exception as exc:
        log.error("create_storage_account: blob service properties failed: %s", exc)
        steps.append({"step": "blob_service_properties", "success": False, "message": str(exc)[:300]})

    try:
        client.file_services.set_service_properties(resource_group, account_name, FileServiceProperties(
            share_delete_retention_policy=DeleteRetentionPolicy(enabled=bool(soft_delete), days=int(soft_delete_days or 30)),
        ))
        steps.append({"step": "file_service_properties", "success": True, "message": "Share soft-delete applied."})
    except Exception as exc:
        log.error("create_storage_account: file service properties failed: %s", exc)
        steps.append({"step": "file_service_properties", "success": False, "message": str(exc)[:300]})

    existing = set()
    try:
        existing = {c.name for c in client.blob_containers.list(resource_group, account_name)}
    except Exception as exc:
        log.error("create_storage_account: listing existing containers failed: %s", exc)

    for cname in (containers or []):
        if cname in existing:
            steps.append({"step": f"container:{cname}", "success": True, "message": "Already exists — skipped."})
            continue
        try:
            client.blob_containers.create(resource_group, account_name, cname,
                                          BlobContainer(public_access="None"))
            steps.append({"step": f"container:{cname}", "success": True, "message": "Created."})
        except Exception as exc:
            log.error("create_storage_account: container '%s' failed: %s", cname, exc)
            steps.append({"step": f"container:{cname}", "success": False, "message": str(exc)[:300]})

    # "success" reflects the storage account itself (the revertable resource) —
    # a sub-step failure (blob props, a container) is real and surfaced via
    # `steps`/`all_steps_ok`, but must not suppress the change-ledger entry for
    # an account that genuinely now exists in Azure (the caller's
    # _record_change only fires on success=True).
    all_ok = all(s["success"] for s in steps)
    change = {
        "target": f"Storage account {account_name} @ {resource_group}",
        "before": None,
        "after": {"name": account_name, "kind": kind, "sku": sku, "access_tier": access_tier,
                 "containers": list(containers or [])},
        "revert_op": "delete_storage_account",
        "revert_params": {"sub": subscription_id, "rg": resource_group, "account": account_name},
    }
    return {"success": True, "dry_run": False, "change": change,
            "message": ("Storage account and all requested sub-resources deployed." if all_ok
                       else "Storage account created, but one or more sub-resources failed — see steps."),
            "resource_ids": {"storage_account": account.id}, "steps": steps, "all_steps_ok": all_ok}


@_guard
def delete_storage_account(subscription_id: str, resource_group: str, account_name: str) -> dict:
    """Delete a storage account this portal created (revert op). Azure has no
    account-level 'restore previous config' — this is delete-only, same model
    as delete_vm/delete_aks_cluster. Blob-level recovery (soft delete /
    versioning, both on by default) is the only recovery path for what was
    inside it, and only up to their configured retention window."""
    try:
        client = _storage_client(subscription_id)
        client.storage_accounts.delete(resource_group, account_name)
        return {"success": True, "message": f"Storage account '{account_name}' deleted."}
    except Exception as exc:
        if _is_not_found(exc):
            return {"success": True, "message": f"Storage account '{account_name}' already gone."}
        log.error("delete_storage_account failed: %s", exc)
        return {"success": False, "message": str(exc)[:300]}


@_guard
def create_object_replication_policy(subscription_id: str, resource_group: str,
                                     source_account: str, source_container: str,
                                     destination_account: str, destination_container: str) -> dict:
    """Best-effort: set up Object Replication between this request's storage
    account (source) and an existing destination account. Azure requires the
    policy to be created on the DESTINATION first (to mint a policy ID), then
    a matching policy on the SOURCE referencing that ID — both calls happen
    here. Called as a separate step after create_storage_account succeeds;
    a failure here does not mean the storage account itself failed."""
    from azure.mgmt.storage.models import ObjectReplicationPolicy, ObjectReplicationPolicyRule
    try:
        client = _storage_client(subscription_id)
        rule = ObjectReplicationPolicyRule(source_container=source_container,
                                           destination_container=destination_container)
        dest_policy = client.object_replication_policies.create_or_update(
            resource_group, destination_account, "default",
            ObjectReplicationPolicy(source_account=source_account,
                                    destination_account=destination_account, rules=[rule]))
        policy_id = dest_policy.policy_id or "default"
        client.object_replication_policies.create_or_update(
            resource_group, source_account, policy_id,
            ObjectReplicationPolicy(source_account=source_account,
                                    destination_account=destination_account, rules=[rule]))
        return {"success": True, "message": f"Object replication policy '{policy_id}' created "
                f"({source_container} -> {destination_account}/{destination_container})."}
    except Exception as exc:
        log.error("create_object_replication_policy failed: %s", exc)
        return {"success": False, "message": str(exc)[:300]}


@_guard
def create_storage_private_endpoint(subscription_id: str, resource_group: str, location: str,
                                    account_name: str, subnet_id: str, tags: dict = None) -> dict:
    """Best-effort: create a Private Endpoint (blob sub-resource) for this
    storage account in the given subnet, auto-approved (same subscription).
    Does NOT link a private DNS zone — that's a separate 'DNS / Private DNS
    Link' request in this portal (see RequestType.DNS), same reuse pattern
    as link_aks_private_dns_to_hub being a distinct step from AKS create.
    Called as a separate step after create_storage_account succeeds; a
    failure here does not mean the storage account itself failed."""
    from azure.mgmt.network.models import PrivateEndpoint, PrivateLinkServiceConnection, Subnet
    try:
        storage = _storage_client(subscription_id).storage_accounts.get_properties(resource_group, account_name)
        client = _network_client(subscription_id)
        pe_name = f"{account_name}-pe-blob"
        pe = client.private_endpoints.begin_create_or_update(
            resource_group, pe_name,
            PrivateEndpoint(location=location, subnet=Subnet(id=subnet_id), tags=_tags(tags),
                           private_link_service_connections=[PrivateLinkServiceConnection(
                               name=f"{account_name}-plsc", private_link_service_id=storage.id,
                               group_ids=["blob"])])).result()
        return {"success": True, "message": f"Private Endpoint '{pe_name}' created for blob. "
                f"Link a private DNS zone separately (raise a 'DNS / Private DNS Link' request) "
                f"for name resolution to work from inside the VNet.", "resource_ids": {"private_endpoint": pe.id}}
    except Exception as exc:
        log.error("create_storage_private_endpoint failed: %s", exc)
        return {"success": False, "message": str(exc)[:300]}
