"""
Resource Optimizer — scans subscriptions for idle / orphaned Azure resources and
adds an AI intelligence layer that prioritises the findings and recommends action.

Uses a SEPARATE, READ-ONLY service principal (OPT_* settings), isolated from the
network-automation and cost credentials. It only needs **Reader** on the scopes
you want scanned. The platform never deletes anything — findings are advisory.

Efficient by design: instead of walking every resource via per-type REST calls,
it runs a handful of Azure Resource Graph (KQL) queries that sweep all scoped
subscriptions at once. Reader is sufficient for Resource Graph.
"""
import logging
import threading
import time
from datetime import datetime, timedelta

import requests
from config import cfg

log = logging.getLogger(__name__)

_ARM = "https://management.azure.com"
_ARG_URL = f"{_ARM}/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01"

_token_lock = threading.Lock()
_token_cache = {"key": None, "token": None, "exp": 0.0}

_SCAN_TTL = 600  # cache a scan for 10 min
_scan_lock = threading.Lock()
_scan_cache = {"exp": 0.0, "result": None}


def configured() -> bool:
    return bool(cfg.OPT_TENANT_ID and cfg.OPT_CLIENT_ID and cfg.OPT_CLIENT_SECRET)


def _token() -> str:
    key = (cfg.OPT_TENANT_ID, cfg.OPT_CLIENT_ID, cfg.OPT_CLIENT_SECRET)
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


def list_subscriptions() -> list:
    """Subscription IDs the optimizer SP will scan (OPT_SUBSCRIPTIONS or all it sees)."""
    allow = [x.strip() for x in (cfg.OPT_SUBSCRIPTIONS or "").split(",") if x.strip()]
    if allow:
        return allow
    resp = requests.get(f"{_ARM}/subscriptions?api-version=2022-12-01",
                        headers=_headers(), timeout=20)
    resp.raise_for_status()
    return [s.get("subscriptionId") for s in resp.json().get("value", []) if s.get("subscriptionId")]


def _sub_names() -> dict:
    """Map of subscription id → display name (best-effort; falls back to id)."""
    try:
        resp = requests.get(f"{_ARM}/subscriptions?api-version=2022-12-01",
                            headers=_headers(), timeout=20)
        resp.raise_for_status()
        return {s["subscriptionId"]: (s.get("displayName") or s["subscriptionId"])
                for s in resp.json().get("value", []) if s.get("subscriptionId")}
    except Exception as exc:
        log.warning("optimize: subscription name lookup failed: %s", exc)
        return {}


def test_connection() -> dict:
    if not configured():
        return {"success": False, "message": "Set the optimizer SP tenant, client ID and secret first."}
    try:
        subs = list_subscriptions()
        # a trivial ARG query proves Resource Graph access too
        _arg("Resources | project id | limit 1", subs)
        return {"success": True,
                "message": f"Connected — optimizer SP can scan {len(subs)} subscription(s)."}
    except Exception as exc:
        log.error("optimize test_connection failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def _arg(query: str, subscriptions: list) -> list:
    """Run one Azure Resource Graph query across the given subscriptions, following
    $skipToken pagination. Returns a list of row dicts."""
    rows, skip = [], None
    while True:
        options = {"$top": 1000, "resultFormat": "objectArray"}
        if skip:
            options["$skipToken"] = skip
        body = {"subscriptions": subscriptions, "query": query, "options": options}
        resp = requests.post(_ARG_URL, headers=_headers(), json=body, timeout=60)
        resp.raise_for_status()
        j = resp.json()
        rows.extend(j.get("data", []) or [])
        skip = j.get("$skipToken")
        if not skip:
            break
    return rows


# ── Cost estimate helpers (approximate, USD/month) ────────────────────────
_DISK_RATE = {  # per GB-month, rough retail
    "Standard_LRS": 0.045, "StandardSSD_LRS": 0.075, "StandardSSD_ZRS": 0.096,
    "Premium_LRS": 0.12, "Premium_ZRS": 0.15, "UltraSSD_LRS": 0.15, "PremiumV2_LRS": 0.12,
}


def _disk_month(sku, gb):
    try:
        return round(_DISK_RATE.get(sku, 0.05) * float(gb or 0), 2)
    except (TypeError, ValueError):
        return None


def _pip_month(sku):
    return 3.65 if (sku or "").lower() == "standard" else 2.60  # static public IP, approx


def _snap_month(gb):
    try:
        return round(0.05 * float(gb or 0), 2)  # snapshot storage, approx
    except (TypeError, ValueError):
        return None


# ── Detectors: (category, label, severity, KQL, row->finding) ─────────────
def _f(cat, label, severity, sub, row, detail, est=None):
    return {"category": cat, "category_label": label, "severity": severity,
            "subscription_id": sub, "resource_group": row.get("resourceGroup", ""),
            "name": row.get("name", ""), "location": row.get("location", ""),
            "resource_id": row.get("id", ""), "detail": detail, "monthly_estimate": est}


def _scan(subscriptions: list, snap_days: int) -> list:
    findings = []

    # 1) Unattached managed disks
    for r in _arg("Resources | where type =~ 'microsoft.compute/disks' "
                  "| where tostring(properties.diskState) == 'Unattached' "
                  "| project id, name, resourceGroup, location, subscriptionId, "
                  "sizeGB=toint(properties.diskSizeGB), sku=tostring(sku.name)", subscriptions):
        findings.append(_f("unattached_disk", "Unattached disk", "medium", r["subscriptionId"], r,
                           f"{r.get('sizeGB','?')} GB {r.get('sku','')} disk not attached to any VM",
                           _disk_month(r.get("sku"), r.get("sizeGB"))))

    # 2) Unassociated public IPs
    for r in _arg("Resources | where type =~ 'microsoft.network/publicipaddresses' "
                  "| where isnull(properties.ipConfiguration) "
                  "| project id, name, resourceGroup, location, subscriptionId, "
                  "sku=tostring(sku.name)", subscriptions):
        findings.append(_f("unassociated_pip", "Unassociated public IP", "medium", r["subscriptionId"], r,
                           f"{r.get('sku','')} public IP not attached to anything",
                           _pip_month(r.get("sku"))))

    # 3) Stopped / deallocated VMs (power state via instance view)
    for r in _arg("Resources | where type =~ 'microsoft.compute/virtualmachines' "
                  "| extend power = tostring(properties.extended.instanceView.powerState.code) "
                  "| where isnotempty(power) and power != 'PowerState/running' "
                  "| project id, name, resourceGroup, location, subscriptionId, power, "
                  "size=tostring(properties.hardwareProfile.vmSize)", subscriptions):
        stopped = r.get("power") == "PowerState/stopped"   # stopped != deallocated → still billed!
        findings.append(_f("stopped_vm" if stopped else "deallocated_vm",
                           "Stopped VM (still billed)" if stopped else "Deallocated VM",
                           "high" if stopped else "low", r["subscriptionId"], r,
                           (f"VM '{r.get('size','')}' is STOPPED (not deallocated) — still incurring "
                            "compute charges. Deallocate or delete." if stopped
                            else f"VM '{r.get('size','')}' deallocated — still paying for its disks.")))

    # 4) Old snapshots
    for r in _arg(f"Resources | where type =~ 'microsoft.compute/snapshots' "
                  f"| extend created = todatetime(properties.timeCreated) "
                  f"| where created < ago({int(snap_days)}d) "
                  f"| project id, name, resourceGroup, location, subscriptionId, "
                  f"sizeGB=toint(properties.diskSizeGB), created=tostring(created)", subscriptions):
        findings.append(_f("old_snapshot", "Stale snapshot", "low", r["subscriptionId"], r,
                           f"Snapshot older than {snap_days} days (created {str(r.get('created',''))[:10]})",
                           _snap_month(r.get("sizeGB"))))

    # 5) Orphaned NSGs (no NICs and no subnets)
    for r in _arg("Resources | where type =~ 'microsoft.network/networksecuritygroups' "
                  "| where (isnull(properties.networkInterfaces) or array_length(properties.networkInterfaces)==0) "
                  "and (isnull(properties.subnets) or array_length(properties.subnets)==0) "
                  "| project id, name, resourceGroup, location, subscriptionId", subscriptions):
        findings.append(_f("orphaned_nsg", "Orphaned NSG", "low", r["subscriptionId"], r,
                           "Network security group not associated with any NIC or subnet"))

    # 6) Orphaned route tables (no subnets)
    for r in _arg("Resources | where type =~ 'microsoft.network/routetables' "
                  "| where isnull(properties.subnets) or array_length(properties.subnets)==0 "
                  "| project id, name, resourceGroup, location, subscriptionId", subscriptions):
        findings.append(_f("orphaned_route_table", "Orphaned route table", "low", r["subscriptionId"], r,
                           "Route table not associated with any subnet"))

    # 7) Unattached NICs
    for r in _arg("Resources | where type =~ 'microsoft.network/networkinterfaces' "
                  "| where isnull(properties.virtualMachine) "
                  "| project id, name, resourceGroup, location, subscriptionId", subscriptions):
        findings.append(_f("unattached_nic", "Unattached NIC", "low", r["subscriptionId"], r,
                           "Network interface not attached to a VM"))

    # 8) Empty resource groups (RGs with zero resources)
    rgs = _arg("ResourceContainers | where type =~ 'microsoft.resources/subscriptions/resourcegroups' "
               "| project id, name, resourceGroup=name, location, subscriptionId", subscriptions)
    nonempty = _arg("Resources | summarize c=count() by subscriptionId, resourceGroup", subscriptions)
    used = {(x["subscriptionId"], (x.get("resourceGroup") or "").lower()) for x in nonempty}
    for r in rgs:
        if (r["subscriptionId"], (r.get("name") or "").lower()) not in used:
            findings.append(_f("empty_rg", "Empty resource group", "low", r["subscriptionId"], r,
                               "Resource group contains no resources"))
    return findings


# ── Usage-pattern detection (Azure Monitor metrics, last 30 days) ─────────
def _metric_stats(resource_id: str, metrics: list, days: int = 30) -> dict:
    """Daily avg/max for the given platform metrics of one resource over `days`.
    Returns {metric_name: {'avg': float|None, 'max': float|None}}. Reader covers
    Microsoft.Insights/metrics/read."""
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    params = {
        "api-version": "2019-07-01",
        "metricnames": ",".join(metrics),
        "aggregation": "Average,Maximum",
        "interval": "P1D",
        "timespan": f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
    }
    resp = requests.get(f"{_ARM}{resource_id}/providers/microsoft.insights/metrics",
                        headers=_headers(), params=params, timeout=30)
    if resp.status_code != 200:
        # Surface Azure's error code/message (e.g. AuthorizationFailed) instead of a bare status.
        try:
            err = (resp.json() or {}).get("error", {}) or {}
            raise RuntimeError(f"{resp.status_code} {err.get('code', '')}: "
                               f"{str(err.get('message', ''))[:140]}".strip())
        except ValueError:
            resp.raise_for_status()
    out = {}
    for m in resp.json().get("value", []):
        name = (m.get("name") or {}).get("value") or ""
        avgs, maxes = [], []
        for ts in m.get("timeseries", []):
            for dp in ts.get("data", []):
                if dp.get("average") is not None:
                    avgs.append(dp["average"])
                if dp.get("maximum") is not None:
                    maxes.append(dp["maximum"])
        out[name] = {"avg": (sum(avgs) / len(avgs)) if avgs else None,
                     "max": max(maxes) if maxes else None}
    return out


def _scan_usage(vms: list, low_avg: float, low_max: float):
    """Flag VMs that were under-utilised over the last 30 days (low CPU). A VM with
    no CPU metric data in the window didn't run (deallocated) and is skipped — that
    presence check is more reliable than Resource Graph's power state.

    Returns (findings, stats) where stats = {found, checked, errored, error}:
      found   — VMs seen; checked — VMs that returned CPU data (i.e. ran);
      errored — VMs whose metrics call failed; error — a sample failure message.
    One metric call per VM, bounded concurrency. Memory/network are reported when
    available (memory needs the Azure Monitor Agent)."""
    from concurrent.futures import ThreadPoolExecutor

    def _check(r):
        try:
            s = _metric_stats(r["id"], ["Percentage CPU", "Available Memory Bytes",
                                        "Network In Total", "Network Out Total"], 30)
        except Exception as exc:
            return ("error", None, str(exc)[:180])
        cpu = s.get("Percentage CPU", {})
        avg, mx = cpu.get("avg"), cpu.get("max")
        if avg is None:
            return ("nodata", None, None)             # no data → didn't run in the window
        if avg < low_avg and (mx is None or mx < low_max):
            detail = (f"VM '{r.get('size','')}' averaged {avg:.1f}% CPU "
                      f"(peak {mx:.0f}%) over 30 days — downsize or deallocate")
            mem = s.get("Available Memory Bytes", {}).get("avg")
            if mem is not None:
                detail += f"; avg free memory {mem / 1e9:.1f} GB"
            net = s.get("Network In Total", {}).get("avg")
            if net is not None and net < 1e7:         # < ~10 MB/day inbound → also quiet
                detail += "; low network traffic"
            return ("ran", _f("underutilized_vm", "Underutilized VM (low CPU)", "medium",
                              r["subscriptionId"], r, detail), None)
        return ("ran", None, None)                    # ran, but adequately utilised

    findings = []
    stats = {"found": len(vms or []), "checked": 0, "errored": 0, "error": None}
    if not vms:
        return findings, stats
    with ThreadPoolExecutor(max_workers=6) as ex:
        for status, res, err in ex.map(_check, vms):
            if status == "error":
                stats["errored"] += 1
                if err and not stats["error"]:
                    stats["error"] = err
                    log.warning("optimize usage: VM metrics failed — %s", err)
            elif status == "ran":
                stats["checked"] += 1
                if res:
                    findings.append(res)
    return findings, stats


# Severity ordering for sorting.
_SEV_RANK = {"high": 3, "medium": 2, "low": 1}


def scan_all(force: bool = False) -> dict:
    """Scan all scoped subscriptions and roll findings up. Cached ~10 min.
    Returns {generated_at, subscriptions, findings, by_category, totals}."""
    now = time.time()
    with _scan_lock:
        if not force and _scan_cache["result"] and now < _scan_cache["exp"]:
            return _scan_cache["result"]

    subs = list_subscriptions()
    snap_days = cfg.OPT_SNAPSHOT_AGE_DAYS or 90
    findings = _scan(subs, snap_days) if subs else []

    # Usage-pattern pass: running VMs under-utilised over the last 30 days (CPU).
    usage = {"enabled": bool(cfg.OPT_USAGE_SCAN), "found": 0, "checked": 0, "errored": 0,
             "flagged": 0, "error": None,
             "avg_threshold": float(cfg.OPT_LOW_CPU_AVG or 5), "peak_threshold": float(cfg.OPT_LOW_CPU_MAX or 20)}
    if subs and cfg.OPT_USAGE_SCAN:
        try:
            # All VMs (not filtered by ARG power state, which is often not indexed);
            # _scan_usage skips VMs with no CPU data (i.e. that didn't run).
            vms = _arg("Resources | where type =~ 'microsoft.compute/virtualmachines' "
                       "| project id, name, resourceGroup, location, subscriptionId, "
                       "size=tostring(properties.hardwareProfile.vmSize)", subs)
            uv, ustat = _scan_usage(vms, usage["avg_threshold"], usage["peak_threshold"])
            findings += uv
            usage.update(found=ustat["found"], checked=ustat["checked"],
                         errored=ustat["errored"], error=ustat["error"], flagged=len(uv))
        except Exception as exc:
            log.exception("optimize: usage-pattern scan failed")
            usage["error"] = str(exc)[:180]

    # attach a friendly subscription name to each finding (for the table + filter)
    names = _sub_names()
    subs_seen = {}
    for f in findings:
        f["subscription_name"] = names.get(f["subscription_id"], f["subscription_id"])
        subs_seen[f["subscription_id"]] = f["subscription_name"]

    # Cost per finding: REAL cost from Cost Management (cost SP) when available,
    # otherwise the retail estimate — so a resource with no billed cost in the
    # window (e.g. created this month, or an access/ID gap) still shows a figure.
    cost_source, cost_timeframe, cost_currency_code = "estimate", None, ""
    cost_matched = 0
    import costmgmt
    if findings and costmgmt.configured():
        try:
            cr = costmgmt.cost_by_resource(subs, timeframe="MonthToDate")
            cmap, cost_currency_code = cr["costs"], cr.get("currency", "")
            for f in findings:
                retail = f.get("monthly_estimate")          # rough estimate from the detector
                real = cmap.get((f.get("resource_id") or "").lower())
                if real is not None:
                    f["monthly_estimate"], f["cost_is_actual"] = real, True
                    cost_matched += 1
                else:
                    f["monthly_estimate"], f["cost_is_actual"] = retail, False
            cost_source = "actual" if cost_matched else "estimate"
            cost_timeframe = "month-to-date"
        except Exception:
            log.exception("optimize: actual-cost lookup failed, keeping estimates")
            for f in findings:
                f["cost_is_actual"] = False
    else:
        for f in findings:
            f["cost_is_actual"] = False

    # roll-ups
    by_cat = {}
    est_total = 0.0
    for f in findings:
        c = by_cat.setdefault(f["category"], {"label": f["category_label"], "count": 0,
                                              "estimate": 0.0, "severity": f["severity"]})
        c["count"] += 1
        if f.get("monthly_estimate"):
            c["estimate"] += f["monthly_estimate"]
            est_total += f["monthly_estimate"]
    for c in by_cat.values():
        c["estimate"] = round(c["estimate"], 2)

    findings.sort(key=lambda f: (_SEV_RANK.get(f["severity"], 0), f.get("monthly_estimate") or 0),
                  reverse=True)
    result = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "subscriptions": len(subs),
        "subscription_names": [{"id": k, "name": v} for k, v in sorted(subs_seen.items(), key=lambda x: x[1].lower())],
        "findings": findings,
        "by_category": by_cat,
        "totals": {"count": len(findings), "monthly_estimate": round(est_total, 2)},
        "currency": cfg.COST_CURRENCY or "$",
        "cost_source": cost_source,           # "actual" (Cost Management) | "estimate"
        "cost_timeframe": cost_timeframe,      # e.g. "last 30 days"
        "cost_currency_code": cost_currency_code,
        "cost_matched": cost_matched,          # findings with a real Cost Management figure
        "cost_sp_configured": costmgmt.configured(),
        "usage": usage,                        # {enabled, checked, flagged, thresholds}
    }
    with _scan_lock:
        _scan_cache.update(exp=now + _SCAN_TTL, result=result)
    return result


def summarize(scan: dict) -> str:
    """AI intelligence layer — a prioritised, plain-language optimisation brief.
    Best-effort: returns None if no LLM is configured or the call fails."""
    import netdiag
    if not netdiag._llm_available():
        return None
    by = scan.get("by_category", {})
    if not by:
        return None
    cur = scan.get("currency", "$")
    actual = scan.get("cost_source") == "actual"
    cost_word = ("actual monthly costs from Azure Cost Management" if actual
                 else "rough retail monthly cost estimates")
    lines = [f"- {v['label']}: {v['count']} item(s), {cur}{v['estimate']}/mo, severity {v['severity']}"
             for v in sorted(by.values(), key=lambda x: x["estimate"], reverse=True)]
    system = (
        "You are a senior Azure FinOps / cloud-optimisation advisor for Presight R&D. You are given a "
        "read-only scan summary of idle and orphaned resources across Azure subscriptions, with "
        f"{cost_word}. Respond in ENGLISH ONLY. Do NOT think out loud or emit any "
        "chain-of-thought / <think> tags in any language — output only the final answer. Write a SHORT "
        "prioritised optimisation brief (max ~170 words, no preamble): (1) the top 2-3 actions that "
        "reclaim the most spend, with the approximate saving; (2) quick hygiene wins (orphaned NSGs / "
        "route tables / NICs / empty groups); and (3) a one-line safety caution to verify each resource "
        "is truly unused before deleting (snapshots/disks may be deliberate backups). Be specific and "
        "practical; do not invent numbers beyond those given.")
    user = (f"Total findings: {scan['totals']['count']}; estimated reclaimable ≈ {cur}"
            f"{scan['totals']['monthly_estimate']}/month across {scan['subscriptions']} subscription(s).\n\n"
            f"By category:\n" + "\n".join(lines))
    try:
        return netdiag._llm_complete(system, user) or None   # '' => leaked/non-English
    except Exception as exc:
        log.error("optimize.summarize failed: %s", exc)
        return None
