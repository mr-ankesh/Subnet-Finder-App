"""
Resource Relationship Graph — read-only (V1) visual map of Azure resource
dependencies for troubleshooting, governance and impact analysis.

Uses a SEPARATE, READ-ONLY service principal (RESGRAPH_* settings), isolated
from the network-automation, cost and optimizer credentials. It only needs
Reader on the scopes you want graphed.

Every call in this module is a read (Azure Resource Graph query, or a typed
SDK `.get()`/`.list()`) — there is no `@_guard`/dry-run wrapper anywhere here,
unlike `azure_tools.py`. That's intentional, not an oversight: nothing in
this module can mutate Azure, so `AZURE_DRY_RUN` has no bearing on it.

Discovery model
----------------
A pure forward walk (follow only the reference IDs a resource's own
properties point at) can't answer roughly half the entry points this
feature's own form allows — e.g. "start at this Route Table" or "start at
this Private DNS Zone" — because those resource types don't carry a property
pointing back at whatever references them. So discovery is:

  1. One Azure Resource Graph (ARG) query per request, scoped to the given
     subscription (and resource group, if given), pulling every resource's
     `id`/`name`/`type`/`resourceGroup`/`properties` in one sweep.
  2. `REFERENCE_PATHS` (below) is a declarative map of, per Azure resource
     `type`, which property paths point at other resource IDs and what edge
     label to use. Walking it over every row builds a forward adjacency map;
     inverting that gives the reverse map. A BFS from the seed resource(s)
     walks **both** directions using these two maps — no extra Azure calls
     needed for the vast majority of edges.
  3. A handful of relationships aren't property references at all (a VNET's
     subnets are embedded sub-objects, not separate ARG rows; an AKS node
     resource group's Load Balancer/Public IP aren't referenced anywhere,
     they just live in that RG; a storage account's blob containers and a
     private endpoint's DNS zone group aren't returned by ARG's `Resources`
     table). Those are resolved with small, best-effort, non-fatal typed-SDK
     calls, only for nodes actually included in the graph — see
     `_expand_subnets`, `_expand_aks_node_rg`, `_expand_storage_containers`,
     `_expand_pe_dns_zone_group`.
"""
import logging
import re
import time
import threading
from collections import defaultdict

import requests
from config import cfg

log = logging.getLogger(__name__)

_ARM = "https://management.azure.com"
_ARG_URL = f"{_ARM}/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01"

_token_lock = threading.Lock()
_token_cache = {"key": None, "token": None, "exp": 0.0}


def configured() -> bool:
    return bool(cfg.RESGRAPH_TENANT_ID and cfg.RESGRAPH_CLIENT_ID and cfg.RESGRAPH_CLIENT_SECRET)


def _token() -> str:
    key = (cfg.RESGRAPH_TENANT_ID, cfg.RESGRAPH_CLIENT_ID, cfg.RESGRAPH_CLIENT_SECRET)
    now = time.time()
    with _token_lock:
        if _token_cache["key"] == key and _token_cache["token"] and now < _token_cache["exp"]:
            return _token_cache["token"]
        from azure.identity import ClientSecretCredential
        cred = ClientSecretCredential(*key)
        tok = cred.get_token(f"{_ARM}/.default")
        _token_cache.update(key=key, token=tok.token, exp=(tok.expires_on - 300))
        return tok.token


def _headers():
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}


def _credential():
    """Typed-SDK credential object for the enrichment/expansion calls."""
    from azure.identity import ClientSecretCredential
    return ClientSecretCredential(cfg.RESGRAPH_TENANT_ID, cfg.RESGRAPH_CLIENT_ID, cfg.RESGRAPH_CLIENT_SECRET)


def list_subscriptions() -> list:
    """Subscriptions the Resource Graph SP can see (id + display name)."""
    resp = requests.get(f"{_ARM}/subscriptions?api-version=2022-12-01", headers=_headers(), timeout=20)
    resp.raise_for_status()
    return [{"id": s["subscriptionId"], "name": s.get("displayName") or s["subscriptionId"]}
            for s in resp.json().get("value", []) if s.get("subscriptionId")]


def test_connection() -> dict:
    if not configured():
        return {"success": False, "message": "Set the Resource Graph SP tenant, client ID and secret first."}
    try:
        subs = list_subscriptions()
        _arg("Resources | project id | limit 1", [s["id"] for s in subs] or None)
        return {"success": True, "message": f"Connected — Resource Graph SP can see {len(subs)} subscription(s)."}
    except Exception as exc:
        log.error("resourcegraph test_connection failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def _arg(query: str, subscriptions) -> list:
    """Run one Azure Resource Graph query, following $skipToken pagination."""
    rows, skip = [], None
    while True:
        options = {"$top": 1000, "resultFormat": "objectArray"}
        if skip:
            options["$skipToken"] = skip
        body = {"query": query, "options": options}
        if subscriptions:
            body["subscriptions"] = subscriptions
        resp = requests.post(_ARG_URL, headers=_headers(), json=body, timeout=60)
        resp.raise_for_status()
        j = resp.json()
        rows.extend(j.get("data", []) or [])
        skip = j.get("$skipToken")
        if not skip:
            break
    return rows


# ── Typed SDK client factories (own credential — never azure_tools._get_credential) ──

def _network_client(subscription_id: str):
    from azure.mgmt.network import NetworkManagementClient
    return NetworkManagementClient(_credential(), subscription_id)


def _storage_client(subscription_id: str):
    from azure.mgmt.storage import StorageManagementClient
    return StorageManagementClient(_credential(), subscription_id)


# ── Resource-type → display category ───────────────────────────────────────

_CATEGORY_BY_PREFIX = [
    ("microsoft.network/virtualnetworks/subnets", "network"),
    ("microsoft.network/virtualnetworks", "network"),
    ("microsoft.network/networksecuritygroups", "network"),
    ("microsoft.network/routetables", "network"),
    ("microsoft.network/azurefirewalls", "network"),
    ("microsoft.network/firewallpolicies", "network"),
    ("microsoft.network/privateendpoints", "network"),
    ("microsoft.network/privatednszones", "network"),
    ("microsoft.network/networkinterfaces", "compute"),
    ("microsoft.network/loadbalancers", "platform"),
    ("microsoft.network/publicipaddresses", "platform"),
    ("microsoft.compute/virtualmachines", "compute"),
    ("microsoft.compute/disks", "compute"),
    ("microsoft.compute/availabilitysets", "compute"),
    ("microsoft.compute/diskencryptionsets", "security"),
    ("microsoft.containerservice/managedclusters", "platform"),
    ("microsoft.managedidentity/userassignedidentities", "security"),
    ("microsoft.keyvault/vaults", "security"),
    ("microsoft.storage/storageaccounts", "storage"),
]


def _category_for_type(rtype: str) -> str:
    rtype = (rtype or "").lower()
    for prefix, cat in _CATEGORY_BY_PREFIX:
        if rtype.startswith(prefix):
            return cat
    return "other"


# ── Declarative reference map: type -> [(path, edge_label)] ────────────────
#
# Path syntax: dot-separated property segments; "[]" after a segment means
# "expand this array, then keep walking"; "{}" after a segment means "this is
# a dict keyed by resource ID — take the keys, not the values" (used for
# `identity.userAssignedIdentities`, which Azure shapes as {id: {...}}).

REFERENCE_PATHS = {
    "microsoft.compute/virtualmachines": [
        ("networkProfile.networkInterfaces[].id", "uses_nic"),
        ("storageProfile.osDisk.managedDisk.id", "uses_disk"),
        ("storageProfile.dataDisks[].managedDisk.id", "uses_disk"),
        ("availabilitySet.id", "member_of"),
        ("identity.userAssignedIdentities{}", "uses_identity"),
    ],
    "microsoft.network/networkinterfaces": [
        ("ipConfigurations[].subnet.id", "attached_to"),
        ("ipConfigurations[].publicIPAddress.id", "uses_public_ip"),
        ("networkSecurityGroup.id", "protected_by"),
    ],
    "microsoft.network/privateendpoints": [
        ("subnet.id", "attached_to"),
        ("privateLinkServiceConnections[].privateLinkServiceId", "connects_to"),
        ("manualPrivateLinkServiceConnections[].privateLinkServiceId", "connects_to"),
    ],
    "microsoft.network/privatednszones/virtualnetworklinks": [
        ("virtualNetwork.id", "linked_to_vnet"),
    ],
    "microsoft.network/privateendpoints/privatednszonegroups": [
        ("privateDnsZoneConfigs[].privateDnsZoneId", "resolves_via"),
    ],
    "microsoft.network/loadbalancers": [
        ("frontendIPConfigurations[].publicIPAddress.id", "uses_public_ip"),
        ("frontendIPConfigurations[].subnet.id", "attached_to"),
    ],
    "microsoft.network/azurefirewalls": [
        ("ipConfigurations[].subnet.id", "attached_to"),
        ("firewallPolicy.id", "uses_policy"),
    ],
    "microsoft.storage/storageaccounts": [
        ("networkAcls.virtualNetworkRules[].virtualNetworkResourceId", "allows_subnet"),
        ("privateEndpointConnections[].privateEndpoint.id", "exposed_via"),
        ("identity.userAssignedIdentities{}", "uses_identity"),
    ],
    "microsoft.containerservice/managedclusters": [
        ("agentPoolProfiles[].vnetSubnetID", "attached_to"),
        ("identityProfile.kubeletidentity.resourceId", "uses_identity"),
        ("identity.userAssignedIdentities{}", "uses_identity"),
    ],
    "microsoft.compute/disks": [
        ("encryption.diskEncryptionSetId", "encrypted_by"),
    ],
    "microsoft.compute/diskencryptionsets": [
        ("activeKey.sourceVault.id", "uses_keyvault"),
    ],
}

# storage account CMK: encryption.keyVaultProperties.keyVaultUri is a URI, not
# a resource ID — resolved against the discovered Key Vault rows by hostname
# match (best-effort; see _resolve_cmk_edges).


def _walk(obj, path: str):
    """Yield every resource-ID-shaped leaf value reached by `path` in `obj`."""
    segments = path.split(".")
    frontier = [obj]
    for seg in segments:
        expand_array = seg.endswith("[]")
        expand_dict_keys = seg.endswith("{}")
        key = seg[:-2] if (expand_array or expand_dict_keys) else seg
        nxt = []
        for item in frontier:
            if not isinstance(item, dict):
                continue
            val = item.get(key)
            if val is None:
                # ARM sub-resources (e.g. a NIC's ipConfigurations[] entries,
                # an LB's frontendIPConfigurations[] entries) commonly wrap
                # their own fields in a nested "properties", same as the
                # top-level resource does — fall through to it transparently
                # rather than requiring every REFERENCE_PATHS entry to know
                # which array items happen to be sub-resource-shaped.
                nested = item.get("properties")
                if isinstance(nested, dict):
                    val = nested.get(key)
            if val is None:
                continue
            if expand_dict_keys:
                if isinstance(val, dict):
                    nxt.extend(val.keys())
            elif expand_array:
                if isinstance(val, list):
                    nxt.extend(val)
            else:
                nxt.append(val)
        frontier = nxt
    for v in frontier:
        if isinstance(v, str) and v:
            yield v


def _extract_edges(row: dict):
    rtype = (row.get("type") or "").lower()
    props = row.get("properties") or {}
    src = row.get("id")
    if not src:
        return
    for path, label in REFERENCE_PATHS.get(rtype, []):
        for target in _walk(props, path):
            yield (src, target, label)


def _parent_id(resource_id: str, strip_segments: int) -> str:
    """Strip the last `strip_segments` path segments off a resource ID to get
    its parent's resource ID (e.g. '.../privateDnsZones/z/virtualNetworkLinks/l'
    strips 2 -> '.../privateDnsZones/z')."""
    parts = resource_id.rstrip("/").split("/")
    return "/".join(parts[:-strip_segments]) if strip_segments < len(parts) else resource_id


def _add_edge(forward: dict, src: str, tgt: str, label: str):
    """The only way edges are added to `forward` — normalizes both ends to
    lowercase so keys always agree with `id_map`/`included` (Azure resource
    IDs are case-insensitive; ARG/SDK responses don't return consistent
    casing). Never append to `forward[...]` directly."""
    if src and tgt:
        forward[src.lower()].append((tgt.lower(), label))


def _node(rid: str, name: str, rtype: str, properties: dict = None) -> dict:
    return {
        "id": rid,
        "name": name,
        "type": rtype,
        "category": _category_for_type(rtype),
        "properties": properties or {},
    }


def _trim_properties(row: dict) -> dict:
    out = {}
    if row.get("location"):
        out["location"] = row["location"]
    if row.get("resourceGroup"):
        out["resourceGroup"] = row["resourceGroup"]
    props = row.get("properties") or {}
    for k in ("provisioningState", "addressSpace", "addressPrefix", "vmSize", "sku"):
        if k in props:
            out[k] = props[k]
    if row.get("sku"):
        out["sku"] = row["sku"]
    return out


def _expand_subnets(vnet_row: dict, id_map: dict, forward: dict):
    """VNET subnets are embedded sub-objects, not their own ARG rows — extract
    them as synthetic nodes/edges so the graph shows Subnet/NSG/RouteTable/PE
    relationships without an extra Azure call."""
    props = vnet_row.get("properties") or {}
    vnet_id = vnet_row["id"]
    for sub in (props.get("subnets") or []):
        sid = sub.get("id")
        if not sid:
            continue
        sprops = sub.get("properties") or {}
        id_map[sid.lower()] = {
            "id": sid, "name": sub.get("name") or sid.split("/")[-1],
            "type": "microsoft.network/virtualnetworks/subnets",
            "resourceGroup": vnet_row.get("resourceGroup"), "location": vnet_row.get("location"),
            "properties": sprops,
        }
        _add_edge(forward, sid, vnet_id, "child_of")
        nsg = (sprops.get("networkSecurityGroup") or {}).get("id")
        if nsg:
            _add_edge(forward, sid, nsg, "protected_by")
        rt = (sprops.get("routeTable") or {}).get("id")
        if rt:
            _add_edge(forward, sid, rt, "routed_by")
        for pe in (sprops.get("privateEndpoints") or []):
            pe_id = pe.get("id")
            if pe_id:
                _add_edge(forward, pe_id, sid, "attached_to")


def _resolve_cmk_edges(rows: list, id_map: dict, forward: dict):
    """Best-effort: storage account CMK is a Key Vault URI, not a resource ID —
    match its hostname against discovered Key Vault rows by name."""
    kv_by_name = {}
    for row in rows:
        if (row.get("type") or "").lower() == "microsoft.keyvault/vaults":
            kv_by_name[(row.get("name") or "").lower()] = row["id"]
    if not kv_by_name:
        return
    for row in rows:
        if (row.get("type") or "").lower() != "microsoft.storage/storageaccounts":
            continue
        uri = (((row.get("properties") or {}).get("encryption") or {})
               .get("keyVaultProperties") or {}).get("keyVaultUri")
        if not uri:
            continue
        m = re.match(r"https://([^.]+)\.vault", uri)
        if m and m.group(1).lower() in kv_by_name:
            _add_edge(forward, row["id"], kv_by_name[m.group(1).lower()], "encrypted_by")


def _expand_aks_node_rg(cluster_row: dict, subscription_id: str, id_map: dict, forward: dict):
    """AKS's Load Balancer/Public IP live in the node resource group — that's
    a structural relationship, not a property reference, so it needs its own
    scoped ARG query. Best-effort: failures here don't break the rest of the
    graph."""
    node_rg = (cluster_row.get("properties") or {}).get("nodeResourceGroup")
    if not node_rg:
        return
    try:
        rows = _arg(
            f"Resources | where resourceGroup =~ '{node_rg}' "
            "| where type in~ ('microsoft.network/loadbalancers','microsoft.network/publicipaddresses') "
            "| project id, name, type, resourceGroup, location, properties",
            [subscription_id])
    except Exception as exc:
        log.warning("resourcegraph: AKS node-RG lookup failed for %s: %s", node_rg, exc)
        return
    for row in rows:
        id_map[row["id"].lower()] = row
        _add_edge(forward, row["id"], cluster_row["id"], "runs_in")
        for src, tgt, label in _extract_edges(row):
            _add_edge(forward, src, tgt, label)


def _expand_storage_containers(account_row: dict, subscription_id: str, id_map: dict, forward: dict):
    """Blob containers aren't returned by ARG's Resources table — list them
    via the typed SDK. Best-effort: a failure here (e.g. network-deny on the
    data plane) doesn't break the rest of the graph."""
    try:
        client = _storage_client(subscription_id)
        rg = account_row.get("resourceGroup")
        name = account_row.get("name")
        for c in client.blob_containers.list(rg, name):
            cid = f"{account_row['id']}/blobServices/default/containers/{c.name}"
            id_map[cid.lower()] = {
                "id": cid, "name": c.name, "type": "microsoft.storage/storageaccounts/blobservices/containers",
                "resourceGroup": rg, "location": account_row.get("location"), "properties": {},
            }
            _add_edge(forward, cid, account_row["id"], "child_of")
    except Exception as exc:
        log.warning("resourcegraph: container listing failed for %s: %s", account_row.get("name"), exc)


def _expand_pe_dns_zone_group(pe_row: dict, subscription_id: str, id_map: dict, forward: dict):
    """A private endpoint's DNS zone group is a child resource ARG doesn't
    surface as its own row — fetch it via the typed SDK. Best-effort."""
    try:
        client = _network_client(subscription_id)
        rg = pe_row.get("resourceGroup")
        name = pe_row.get("name")
        for grp in client.private_dns_zone_groups.list(rg, name):
            gid = grp.id
            configs = [{"privateDnsZoneId": c.private_dns_zone_id} for c in (grp.private_dns_zone_configs or [])]
            id_map[gid.lower()] = {
                "id": gid, "name": grp.name, "type": "microsoft.network/privateendpoints/privatednszonegroups",
                "resourceGroup": rg, "location": pe_row.get("location"),
                "properties": {"privateDnsZoneConfigs": configs},
            }
            _add_edge(forward, gid, pe_row["id"], "child_of")
            for cfg_ in configs:
                if cfg_["privateDnsZoneId"]:
                    _add_edge(forward, gid, cfg_["privateDnsZoneId"], "resolves_via")
    except Exception as exc:
        log.warning("resourcegraph: DNS zone group lookup failed for %s: %s", pe_row.get("name"), exc)


def build_graph(subscription_id: str, resource_group: str = None,
                 resource_type: str = None, resource_name: str = None) -> dict:
    max_nodes = int(cfg.RESGRAPH_MAX_NODES or 300)
    max_hops = int(cfg.RESGRAPH_MAX_HOPS or 3)

    query = "Resources"
    if resource_group:
        query += f" | where resourceGroup =~ '{resource_group}'"
    query += " | project id, name, type, resourceGroup, location, properties"
    rows = _arg(query, [subscription_id])

    id_map = {r["id"].lower(): r for r in rows if r.get("id")}
    forward = defaultdict(list)
    for row in rows:
        for src, tgt, label in _extract_edges(row):
            _add_edge(forward, src, tgt, label)
        if (row.get("type") or "").lower() == "microsoft.network/virtualnetworks":
            _expand_subnets(row, id_map, forward)
        # Any nested ARM child resource (type has 2+ slashes, e.g.
        # microsoft.network/privatednszones/virtualnetworklinks) gets a
        # child_of edge to its parent — otherwise rooting the graph directly
        # at the parent (e.g. a Private DNS Zone with no name given for the
        # link) finds it via the reverse index, but rooting AT the child
        # itself, or expecting the parent to show its children, needs this
        # explicit edge since ARG doesn't nest child rows under the parent.
        rtype = (row.get("type") or "").lower()
        if rtype.count("/") >= 2 and row.get("id"):
            parent = _parent_id(row["id"], 2)
            if parent.lower() != row["id"].lower():
                _add_edge(forward, row["id"], parent, "child_of")
    _resolve_cmk_edges(rows, id_map, forward)

    reverse = defaultdict(list)
    for src, edges in forward.items():
        for tgt, label in edges:
            reverse[tgt.lower()].append((src, label))

    def neighbors(rid: str):
        out = []
        for tgt, label in forward.get(rid, []):
            out.append((tgt.lower(), label))
        for src, label in reverse.get(rid.lower(), []):
            out.append((src.lower(), label))
        return out

    # ── Seed resolution ─────────────────────────────────────────────────
    # Three modes, matching the form: a specific resource (name, optionally
    # narrowed by type); every resource of a given type with no name; or the
    # whole scope. The middle case matters — the form allows "Resource Type
    # = Route Table, no name" on its own, and it must not silently fall back
    # to "everything in scope" and drop the type filter the user picked.
    root_id = None
    rtype_needle = (resource_type or "").lower()
    if resource_name:
        needle = resource_name.lower()
        for rid, row in id_map.items():
            if (row.get("name") or "").lower() == needle and (
                    not rtype_needle or (row.get("type") or "").lower() == rtype_needle):
                root_id = rid
                break
        if not root_id:
            return {"nodes": [], "edges": [], "root": None, "truncated": False,
                    "truncated_at_hop": None, "hop_limit": max_hops,
                    "error": f"No resource named '{resource_name}' found in scope."}
        seed = [root_id]
    elif rtype_needle:
        seed = [rid for rid, row in id_map.items()
                if (row.get("type") or "").lower() == rtype_needle][:max_nodes]
        if not seed:
            return {"nodes": [], "edges": [], "root": None, "truncated": False,
                    "truncated_at_hop": None, "hop_limit": max_hops,
                    "error": f"No resources of type '{resource_type}' found in scope."}
    else:
        seed = list(id_map.keys())[:max_nodes]

    # ── Hop-bounded BFS, truncated by complete hop level (deterministic) ──
    included = set(seed)
    edges_out = []
    edge_seen = set()
    frontier = list(seed)
    truncated = False
    truncated_at_hop = None
    hop = 0
    while frontier and hop < max_hops:
        hop += 1
        next_frontier = []
        candidate_new = set()
        hop_edges = []
        for rid in frontier:
            for nrid, label in neighbors(rid):
                if nrid not in id_map:
                    continue
                key = tuple(sorted((rid, nrid))) + (label,)
                if key not in edge_seen:
                    edge_seen.add(key)
                    hop_edges.append((rid, nrid, label))
                if nrid not in included:
                    candidate_new.add(nrid)
        if len(included) + len(candidate_new) > max_nodes:
            truncated = True
            truncated_at_hop = hop
            edges_out.extend(e for e in hop_edges if e[0] in included and e[1] in included)
            break
        included.update(candidate_new)
        next_frontier.extend(candidate_new)
        edges_out.extend(hop_edges)
        frontier = next_frontier

    # ── Best-effort structural expansions for included nodes ─────────────
    # Snapshot forward *before* expanding, so only edges the expansion
    # functions themselves add get pulled in below — not the pre-existing
    # global reference graph (which would silently defeat hop truncation by
    # re-admitting every node reachable from an already-included one).
    forward_before = {k: list(v) for k, v in forward.items()}
    for rid in list(included):
        row = id_map.get(rid)
        if not row:
            continue
        rtype = (row.get("type") or "").lower()
        if rtype == "microsoft.containerservice/managedclusters":
            _expand_aks_node_rg(row, subscription_id, id_map, forward)
        elif rtype == "microsoft.storage/storageaccounts":
            _expand_storage_containers(row, subscription_id, id_map, forward)
        elif rtype == "microsoft.network/privateendpoints":
            _expand_pe_dns_zone_group(row, subscription_id, id_map, forward)
    # pull in only the NEW edges the expansions above actually added
    for rid, edges in forward.items():
        new_edges = edges[len(forward_before.get(rid, [])):]
        if not new_edges:
            continue
        for tgt, label in new_edges:
            if tgt.lower() not in id_map or len(included) >= max_nodes:
                continue
            included.add(rid.lower())
            included.add(tgt.lower())
            key = tuple(sorted((rid.lower(), tgt.lower()))) + (label,)
            if key not in edge_seen:
                edge_seen.add(key)
                edges_out.append((rid.lower(), tgt.lower(), label))

    nodes = []
    for rid in included:
        row = id_map[rid]
        nodes.append(_node(row["id"], row.get("name") or row["id"].split("/")[-1],
                            row.get("type") or "unknown", _trim_properties(row)))
    # edges_out holds internal (lowercase) keys; map back to each node's
    # properly-cased "id" so edge source/target exactly match node ids.
    edges = [{"source": id_map[s]["id"], "target": id_map[t]["id"], "label": label}
             for s, t, label in edges_out if s in included and t in included]

    return {
        "nodes": nodes,
        "edges": edges,
        "root": id_map[root_id]["id"] if root_id else None,
        "truncated": truncated,
        "truncated_at_hop": truncated_at_hop,
        "hop_limit": max_hops,
    }
