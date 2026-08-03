"""
Deterministic subnet/VNET arithmetic — advisor_kb/composer/network_sizing.yaml
applied in code. This is the single highest-risk module in the environment
composer: the output goes to TechOps for CIDR approval, and every figure
must be reproducible from this module alone, never estimated by an LLM.

Two sizing numbers exist for the AKS subnet and must NOT be collapsed into
one function — see size_aks_subnet()'s docstring.
"""
import functools
import math

from advisor.catalog_loader import get_composer_file, get_platform_constants

_VNET_BOUNDARIES = [256, 512, 1024, 2048]  # /24, /23, /22, /21
_BOUNDARY_TO_PREFIX = {256: "/24", 512: "/23", 1024: "/22", 2048: "/21"}
_UTILISATION_THRESHOLD = 75  # strictly_greater — see network_sizing.yaml


@functools.lru_cache(maxsize=1)
def _sizing() -> dict:
    return get_composer_file("network_sizing.yaml")


@functools.lru_cache(maxsize=1)
def _subnets_by_id() -> dict:
    return {s["id"]: s for s in _sizing()["subnets"]}


def _bucket_lookup(sizing_table: list, count: int) -> dict:
    """Each row's `nodes`/`vms`/`endpoints` key reads "up to N" — find the
    first row whose N is >= count. If count exceeds every bucket, use the
    last (largest) row rather than raising: an environment this large should
    surface the >/21 deviation/guard downstream, not crash the planner."""
    for row in sizing_table:
        label = row.get("nodes") or row.get("vms") or row.get("endpoints")
        n = int(label.split()[-1])
        if count <= n:
            return row
    return sizing_table[-1]


def size_aks_subnet(node_count: int) -> dict:
    """Two distinct numbers, both returned, never merged:

    - `size`/`total`/`usable` come from a straight bucket lookup against
      snet_aks.sizing_table ("up to 10" -> /26, etc.) — this is what TechOps
      actually allocates, and for 6 nodes it already lands on /26 with no
      override needed.
    - `actual_surge`/`actual_min_addresses` are computed live from the REAL
      node count for the prose explanation ("6 nodes + surge headroom ≈ 13
      today"): surge = max(1, round(0.33 * node_count)),
      min_addresses = node_count + surge + 5. For 6 nodes: round(1.98) = 2,
      6+2+5 = 13 — matching worked_example.md exactly.

      This is a DIFFERENT formula from the one that reproduces the bucket
      table's own `min_addresses` column (floor(0.33 * bucket_ceiling), e.g.
      floor(0.33*10)=3 -> 18), which uses the bucket's ceiling, not the
      actual count, and floor instead of round. Do not "simplify" these into
      one function — they answer different questions (what size to
      allocate, vs. why today's count fits inside it) and verification
      checks each against its own source.
    """
    subnet = _subnets_by_id()["snet_aks"]
    row = _bucket_lookup(subnet["sizing_table"], node_count)
    prefix = row["recommended"]
    total, usable = _prefix_total_usable(prefix)

    actual_surge = max(1, round(0.33 * node_count))
    actual_min_addresses = node_count + actual_surge + 5

    return {
        "id": "snet_aks",
        "purpose": subnet["purpose"],
        "size": prefix,
        "total": total,
        "usable": usable,
        "actual_surge": actual_surge,
        "actual_min_addresses": actual_min_addresses,
        "basis": (
            f"Overlay: only nodes take VNET IPs. {node_count} nodes + surge "
            f"headroom ≈ {actual_min_addresses} today; {prefix} carries "
            f"~{_approx_capacity_nodes(usable)} nodes."
        ),
    }


def _approx_capacity_nodes(usable: int) -> int:
    # Inverse of the same "node + 33% surge + 5 reserved" relationship, for
    # the human-readable "carries ~N nodes" phrase only — never used to pick
    # a size, only to narrate the one already picked.
    return max(0, math.floor((usable - 5) / 1.33))


def size_vm_subnet(vm_count: int) -> dict:
    subnet = _subnets_by_id()["snet_workload"]
    row = _bucket_lookup(subnet["sizing_table"], vm_count)
    prefix = row["recommended"]
    total, usable = _prefix_total_usable(prefix)
    return {
        "id": "snet_workload",
        "purpose": subnet["purpose"],
        "size": prefix,
        "total": total,
        "usable": usable,
        "basis": f"{vm_count} VMs today, headroom to {usable}.",
    }


def size_pe_subnet(endpoint_count: int) -> dict:
    """`recommended_default` ("/27") overrides the raw bucket lookup here —
    a genuine KB judgment call, not silently resolved: the bucket table says
    "up to 10 endpoints -> /28", but the KB's own stated reason ("private
    endpoints accumulate — every new PaaS service adds one") overrides it to
    /27, and the canonical worked example (4 endpoints -> /27) confirms this
    is the intended reading, not the raw per-count bucket. If a KB author
    later removes `recommended_default`, this falls back to the bucket
    table automatically."""
    subnet = _subnets_by_id()["snet_pe"]
    default = subnet.get("recommended_default")
    if default:
        prefix = default
    else:
        row = _bucket_lookup(subnet["sizing_table"], endpoint_count)
        prefix = row["recommended"]
    total, usable = _prefix_total_usable(prefix)
    return {
        "id": "snet_pe",
        "purpose": subnet["purpose"],
        "size": prefix,
        "total": total,
        "usable": usable,
        "basis": f"{', '.join(_pe_service_names(endpoint_count))}. These accumulate.",
    }


def _pe_service_names(endpoint_count: int) -> list:
    # Purely descriptive ordering for the sizing basis text — actual PE list
    # construction (with sub-resource + DNS zone) lives in
    # private_endpoints_for(), this is just the short human list.
    names = ["PostgreSQL", "Key Vault", "ACR", "Storage"]
    return names[:max(1, endpoint_count)] or names


def size_appgw_subnet() -> dict:
    subnet = _subnets_by_id()["snet_appgw"]
    prefix = subnet["recommended_size"]
    total, usable = _prefix_total_usable(prefix)
    return {
        "id": "snet_appgw",
        "purpose": subnet["purpose"],
        "size": prefix,
        "total": total,
        "usable": usable,
        "basis": "Dedicated subnet, required. Sized so the gateway can autoscale later.",
    }


def _prefix_total_usable(prefix: str) -> tuple:
    for row in _sizing()["cidr_reference"]:
        if row["prefix"] == prefix:
            return row["total"], row["usable"]
    raise ValueError(f"Unknown CIDR prefix in network_sizing.yaml: {prefix}")


def pod_cidr_info(aks_present: bool) -> dict:
    """Deliberately NOT a subnet — never insert this into the subnet list or
    add its size into the VNET arithmetic. It's a separate address space
    that only nodes' sibling ranges must avoid overlapping."""
    if not aks_present:
        return None
    pod = _sizing()["pod_cidr"]
    return {
        "cidr": pod["default"],
        "is_subnet": False,
        "note": (
            f"Pod CIDR {pod['default']} — separate from the VNET address space. "
            "Must not overlap the VNET pools, the hub, or the VPN and ZPA ranges."
        ),
    }


def private_endpoints_for(answers: dict, inferred_ids: set) -> list:
    """Builds the {service, sub_resource, dns_zone} list. Reuses
    platform_constants.yaml's own private_dns_zones values — never
    hardcodes a new zone string here."""
    zones = get_platform_constants()["private_dns_zones"]
    entries = []
    if int(answers.get("postgres_count") or 0) > 0:
        entries.append({"service": "PostgreSQL Flexible Server",
                         "sub_resource": "postgresqlServer",
                         "dns_zone": zones["postgres"]})
    if "keyvault_premium_private" in inferred_ids:
        entries.append({"service": "Key Vault", "sub_resource": "vault",
                         "dns_zone": zones["keyvault"]})
    if "container_registry" in inferred_ids:
        entries.append({"service": "Container Registry", "sub_resource": "registry",
                         "dns_zone": zones["acr"]})
    if int(answers.get("storage_count") or 0) > 0:
        entries.append({"service": "Storage Account", "sub_resource": "blob",
                         "dns_zone": zones["blob"]})
    return entries


def aks_private_zone_note(aks_present: bool) -> str:
    if not aks_present:
        return None
    zones = get_platform_constants()["private_dns_zones"]
    return f"Plus the AKS private cluster zone ({zones['aks']}), since the API server is private."


def required_subnets(answers: dict, inferred_ids: set) -> list:
    """Which subnet ids this environment needs, in the worked example's
    display order (gateway, AKS, workload, private endpoints)."""
    subnets = []
    if int(answers.get("appgw_count") or 0) > 0 or "appgw_public_cloudflare" in inferred_ids:
        subnets.append(size_appgw_subnet())
    if int(answers.get("aks_count") or 0) > 0:
        subnets.append(size_aks_subnet(int(answers.get("_aks_node_count") or 0)))
    if int(answers.get("vm_count") or 0) > 0:
        subnets.append(size_vm_subnet(int(answers["vm_count"])))
    any_paas = (int(answers.get("postgres_count") or 0) > 0
                or int(answers.get("storage_count") or 0) > 0
                or "keyvault_premium_private" in inferred_ids
                or "container_registry" in inferred_ids)
    if any_paas:
        endpoints = private_endpoints_for(answers, inferred_ids)
        subnets.append(size_pe_subnet(len(endpoints)))
    return subnets


def compute_vnet_plan(subnets: list) -> dict:
    """Sums TOTAL addresses (never usable, never the Pod CIDR — see
    network_sizing.yaml's vnet_sizing.method), rounds up to the next
    boundary, and flags utilisation STRICTLY greater than 75% — not >=. The
    canonical positive example lands on exactly 75.0% and must NOT trip the
    flag; that's the case this comparison is guarding against."""
    total = sum(s["total"] for s in subnets)
    capacity = next((b for b in _VNET_BOUNDARIES if b >= total), _VNET_BOUNDARIES[-1])
    utilisation_pct = round((total / capacity) * 100, 1) if capacity else 0.0
    flag_tripped = utilisation_pct > _UTILISATION_THRESHOLD
    return {
        "arithmetic_terms": [s["total"] for s in subnets],
        "arithmetic_sum": total,
        "vnet_size": _BOUNDARY_TO_PREFIX[capacity],
        "capacity": capacity,
        "utilisation_pct": utilisation_pct,
        "spare": capacity - total,
        "flag_tripped": flag_tripped,
    }


def mandatory_spoke_wiring() -> list:
    """The fixed hub-integration checklist for any new spoke — verbatim from
    network_sizing.yaml, not re-authored here."""
    return _sizing()["mandatory_spoke_wiring"]


def build_network_plan(answers: dict, inferred_ids: set) -> dict:
    """Orchestrates the whole network plan. `answers` must carry
    `_aks_node_count` (parsed from the aks_scale answer) alongside the
    standard *_count fields — see composition_engine.py for how that's
    threaded through."""
    aks_present = int(answers.get("aks_count") or 0) > 0
    subnets = required_subnets(answers, inferred_ids)
    vnet = compute_vnet_plan(subnets)
    endpoints = private_endpoints_for(answers, inferred_ids)
    return {
        "vnet_count": 1,
        "subnets": subnets,
        **vnet,
        "pod_cidr": pod_cidr_info(aks_present),
        "aks_private_zone_note": aks_private_zone_note(aks_present),
        "private_endpoints": endpoints,
    }
