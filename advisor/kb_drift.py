"""
Advisor KB drift check — a read-only report comparing what the active KB
asserts against reality, from two sources:

LOCAL — the KB vs. this app's own config.py defaults. Zero Azure calls. This
is the highest-value check: it's exactly what would have caught the AKS
Overlay error on day one (the KB described classic CNI while
AKS_NETWORK_PLUGIN_MODE defaulted to overlay) — a mismatch here means the
KB and the app's actual deploy-time behaviour have diverged.

AZURE — read-only, reusing azure_tools.py's EXISTING lookup functions (no
new Azure calls written for this), scoped to ADVISOR_KB_DRIFT_SUBSCRIPTION_ID:
do the VM SKU families and curated images the KB names still exist/resolve
in the configured region.

Both sources report matches/mismatches; mismatches are ADVISORY ONLY — this
module never writes back to the KB or to config.py. A third, explicit
category (unverifiable()) lists what genuinely can't be checked this way —
private DNS zone names, policy assignments, and anything sourced from the
Microsoft/Kyndryl design documents — rather than silently passing them as
if verified.
"""
import logging

from advisor import catalog_loader
from advisor.catalog_loader import AdvisorKBError
from config import cfg

log = logging.getLogger(__name__)


def _parse(files: dict, path: str):
    if path not in files:
        return None
    try:
        return catalog_loader._load_yaml_text(path, files[path])
    except AdvisorKBError:
        return None


def _catalog(files: dict) -> dict:
    out = {}
    for path in files:
        if path.startswith("catalog/") and path.endswith(".yaml") and not path.rsplit("/", 1)[-1].startswith("_"):
            data = _parse(files, path)
            if isinstance(data, dict) and data.get("id"):
                out[data["id"]] = data
    return out


def _row(check, kb_path, config_key, kb_value, config_value, match):
    return {"check": check, "kb_path": kb_path, "config_key": config_key,
            "kb_value": kb_value, "config_value": config_value, "match": match}


# ── LOCAL: KB vs. config.py — no Azure call ─────────────────────────────────

def check_local(files: dict) -> list:
    """Every row here is a real, evidenced assertion found in the shipped
    KB — not a guessed/generic check. Declarative and small on purpose: add
    a row only when the KB genuinely names a config.py setting somewhere."""
    rows = []
    catalog = _catalog(files)
    aks = catalog.get("aks_private_standard") or {}
    aks_design = aks.get("design") or {}
    network_plugin_note = str(aks_design.get("network_plugin", ""))

    kb_says_overlay = "overlay" in network_plugin_note.lower()
    config_mode = cfg.AKS_NETWORK_PLUGIN_MODE
    rows.append(_row(
        "AKS network plugin mode", "catalog/aks_private_standard.yaml: design.network_plugin",
        "AKS_NETWORK_PLUGIN_MODE", network_plugin_note, config_mode,
        kb_says_overlay == (config_mode == "overlay")))

    kb_says_azure_cni = "cni" in network_plugin_note.lower()
    config_plugin = cfg.AKS_NETWORK_PLUGIN
    rows.append(_row(
        "AKS network plugin", "catalog/aks_private_standard.yaml: design.network_plugin",
        "AKS_NETWORK_PLUGIN", network_plugin_note, config_plugin,
        kb_says_azure_cni == (config_plugin == "azure")))

    sizing = _parse(files, "composer/network_sizing.yaml") or {}
    pod_cidr_kb = (sizing.get("pod_cidr") or {}).get("default")
    if pod_cidr_kb is not None:
        rows.append(_row(
            "AKS Pod CIDR", "composer/network_sizing.yaml: pod_cidr.default", "AKS_POD_CIDR",
            pod_cidr_kb, cfg.AKS_POD_CIDR, pod_cidr_kb == cfg.AKS_POD_CIDR))

    constants = _parse(files, "rules/platform_constants.yaml") or {}
    region_kb = (constants.get("region") or {}).get("primary")
    if region_kb is not None:
        for config_key in ("VM_DEFAULT_REGION", "STORAGE_DEFAULT_REGION"):
            config_value = getattr(cfg, config_key)
            rows.append(_row(
                "Default region", "rules/platform_constants.yaml: region.primary", config_key,
                region_kb, config_value, region_kb == config_value))

    return rows


# ── AZURE: read-only, existing lookup functions only ────────────────────────

_VM_SKU_FAMILY_LETTERS = ("D", "E", "F")  # from vm_workload_standard.yaml's own
                                           # design.sizing_guidance prose — "D-series
                                           # general purpose, E-series memory-heavy,
                                           # F-series compute-heavy" — hand-extracted,
                                           # not a generic scan of arbitrary text.


def check_azure(subscription_id: str, region: str) -> list:
    """Best-effort: each check is isolated so one unreachable lookup doesn't
    void the rest of the report. Reuses azure_tools.py's list_vm_skus/
    list_vm_images exactly as they already exist — no new Azure SDK calls."""
    if not subscription_id or not region:
        return []
    rows = []
    try:
        import azure_tools
        res = azure_tools.list_vm_skus(subscription_id, region)
        if res.get("success"):
            # Real Azure family strings are PascalCase, no underscore, "Family"
            # suffix (e.g. "StandardDadsv7Family") — confirmed live against
            # the sandbox subscription; the underscored "Standard_D" form is
            # the display convention (Standard_D2s_v3), not the family field.
            families = {s["family"] for s in res["skus"] if s.get("family")}
            for letter in _VM_SKU_FAMILY_LETTERS:
                prefix = f"standard{letter.lower()}"
                available = any(f.lower().startswith(prefix) for f in families)
                rows.append({"check": f"VM SKU family '{letter}-series' available in {region}",
                            "result": "available" if available else "NOT available",
                            "match": available})
        else:
            rows.append({"check": "VM SKU family availability", "result": f"lookup failed: {res.get('message')}",
                        "match": None})
    except Exception as exc:
        log.warning("kb_drift: VM SKU family check failed: %s", exc)
        rows.append({"check": "VM SKU family availability", "result": f"lookup failed: {exc}", "match": None})

    try:
        import azure_tools
        res = azure_tools.list_vm_images(subscription_id, region)
        if res.get("success"):
            for img in res["images"]:
                ok = img.get("version") is not None
                label = img.get("name") or f"{img.get('publisher')}:{img.get('offer')}:{img.get('sku')}"
                rows.append({"check": f"Curated image '{label}' resolves in {region}",
                            "result": "resolves" if ok else f"failed: {img.get('error')}",
                            "match": ok})
        else:
            rows.append({"check": "Curated image resolution", "result": f"lookup failed: {res.get('message')}",
                        "match": None})
    except Exception as exc:
        log.warning("kb_drift: curated image check failed: %s", exc)
        rows.append({"check": "Curated image resolution", "result": f"lookup failed: {exc}", "match": None})

    return rows


def unverifiable() -> list:
    """Honest, static — never a fabricated check that can't really verify
    these. Listed as manual-review items, per spec."""
    return [
        "Private DNS zone names (platform_constants.yaml's private_dns_zones "
        "are asserted, not queryable the way a SKU is — no Azure API confirms "
        "'this is the zone name the org actually uses').",
        "Policy assignments (whether an Azure Policy actually enforces a "
        "security_floor value like public_network_access: Disabled) — the "
        "portal doesn't have Policy Reader access by default.",
        "Any assertion whose source: cites the Microsoft/Kyndryl design "
        "documents rather than config.py or a live Azure lookup — those are "
        "organisational decisions, not facts this app can re-derive.",
        "Managed-disk SKU availability — list_disk_skus() is config-derived "
        "(VM_OS_DISK_TYPES/VM_DATA_DISK_TYPES), not a live Azure call, so it "
        "cannot confirm regional availability the way list_vm_skus can.",
        "AKS Kubernetes version constraints, if any pattern names one — not "
        "currently asserted by any catalog pattern, so there is nothing to "
        "check against list_aks_versions this round.",
    ]


def run(files: dict, subscription_id: str = None, region: str = None) -> dict:
    """files: the active KB's file set (route layer passes _kb_active_files()).
    Returns {matches, mismatches, unverifiable} — mismatches are advisory
    only, nothing is auto-applied."""
    local_rows = check_local(files)
    azure_rows = check_azure(subscription_id or cfg.ADVISOR_KB_DRIFT_SUBSCRIPTION_ID,
                             region or cfg.VM_DEFAULT_REGION)
    all_rows = local_rows + azure_rows
    matches = [r for r in all_rows if r.get("match") is True]
    mismatches = [r for r in all_rows if r.get("match") is False]
    inconclusive = [r for r in all_rows if r.get("match") is None]
    return {"ok": True, "matches": matches, "mismatches": mismatches,
            "inconclusive": inconclusive, "unverifiable": unverifiable()}
