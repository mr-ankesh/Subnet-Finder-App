"""
Subscription cost dashboard — uses a SEPARATE service principal (COST_* settings)
isolated from the network-automation credentials. Talks to the Azure Cost
Management + Resource Manager REST APIs directly with a client-credential token.

Read-only. The cost SP needs, on each reported scope:
  * Cost Management Reader  (to run cost queries)
  * Reader                  (to list subscriptions)
"""
import logging
import threading
import time
from datetime import datetime

import requests
from config import cfg

log = logging.getLogger(__name__)

_ARM = "https://management.azure.com"
_QUERY_API = "2023-11-01"
_SUBS_API = "2022-12-01"

# Cache the AAD token so we don't re-authenticate on every single REST call.
# A cost summary fans out one query per subscription; without this cache each of
# those (plus the subscription list) did a fresh client-credential round-trip to
# Azure AD, which dominated page-load time. Keyed on the SP config so a live
# credential change in Settings invalidates it.
_token_lock = threading.Lock()
_token_cache = {"key": None, "token": None, "exp": 0.0}


def configured() -> bool:
    return bool(cfg.COST_TENANT_ID and cfg.COST_CLIENT_ID and cfg.COST_CLIENT_SECRET)


def _token() -> str:
    key = (cfg.COST_TENANT_ID, cfg.COST_CLIENT_ID, cfg.COST_CLIENT_SECRET)
    now = time.time()
    with _token_lock:
        if _token_cache["key"] == key and _token_cache["token"] and now < _token_cache["exp"]:
            return _token_cache["token"]
        from azure.identity import ClientSecretCredential
        cred = ClientSecretCredential(*key)
        tok = cred.get_token(f"{_ARM}/.default")
        _token_cache.update(key=key, token=tok.token,
                            exp=(tok.expires_on - 300))  # refresh 5 min early
        return tok.token


def _headers():
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}


def test_connection() -> dict:
    """Settings-side check: can the cost SP authenticate + list subscriptions?"""
    if not configured():
        return {"success": False, "message": "Set the cost SP tenant, client ID and secret first."}
    try:
        subs = list_subscriptions()
        return {"success": True,
                "message": f"Connected — cost SP can see {len(subs)} subscription(s)."}
    except Exception as exc:
        log.error("cost test_connection failed: %s", exc)
        return {"success": False, "message": str(exc)[:200]}


def list_subscriptions() -> list:
    """Subscriptions the cost SP can see (respecting COST_SUBSCRIPTIONS if set)."""
    resp = requests.get(f"{_ARM}/subscriptions?api-version={_SUBS_API}",
                        headers=_headers(), timeout=20)
    resp.raise_for_status()
    subs = [{"id": s.get("subscriptionId"), "name": s.get("displayName") or s.get("subscriptionId"),
             "state": s.get("state")} for s in resp.json().get("value", [])]
    allow = [x.strip() for x in (cfg.COST_SUBSCRIPTIONS or "").split(",") if x.strip()]
    if allow:
        subs = [s for s in subs if s["id"] in allow]
    subs.sort(key=lambda s: s["name"].lower())
    return subs


def _query(subscription_id: str, body: dict, _retries: int = 4) -> dict:
    # Cost Management is aggressively throttled (429). When we fan out one query
    # per subscription we hit it, so honour Retry-After and back off rather than
    # dropping the subscription's spend.
    url = (f"{_ARM}/subscriptions/{subscription_id}"
           f"/providers/Microsoft.CostManagement/query?api-version={_QUERY_API}")
    for attempt in range(_retries + 1):
        resp = requests.post(url, headers=_headers(), json=body, timeout=45)
        if resp.status_code == 429 and attempt < _retries:
            wait = float(resp.headers.get("Retry-After") or (2 ** attempt))
            time.sleep(min(wait, 30))
            continue
        resp.raise_for_status()
        return resp.json().get("properties", {})
    resp.raise_for_status()
    return resp.json().get("properties", {})


def _mg_query(mg_id: str, body: dict, _retries: int = 3) -> dict:
    """Same query at management-group scope — lets one call cover every child
    subscription (grouped by SubscriptionId) instead of one call per subscription."""
    url = (f"{_ARM}/providers/Microsoft.Management/managementGroups/{mg_id}"
           f"/providers/Microsoft.CostManagement/query?api-version={_QUERY_API}")
    for attempt in range(_retries + 1):
        resp = requests.post(url, headers=_headers(), json=body, timeout=60)
        if resp.status_code == 429 and attempt < _retries:
            time.sleep(min(float(resp.headers.get("Retry-After") or (2 ** attempt)), 30))
            continue
        resp.raise_for_status()
        return resp.json().get("properties", {})
    resp.raise_for_status()
    return resp.json().get("properties", {})


def _cols(props):
    return {c.get("name"): i for i, c in enumerate(props.get("columns", []))}


def _mg_totals(timeframe: str) -> dict:
    """{subscription_id: cost} for every subscription under the configured
    management group, in a SINGLE query. Returns (totals, currency)."""
    mg = (cfg.COST_MANAGEMENT_GROUP or "").strip()
    if not mg:
        return None
    body = {"type": "ActualCost", "timeframe": timeframe,
            "dataset": {"granularity": "None",
                        "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                        "grouping": [{"type": "Dimension", "name": "SubscriptionId"}]}}
    props = _mg_query(mg, body)
    col, rows = _cols(props), props.get("rows", [])
    ci = col.get("Cost", col.get("PreTaxCost", 0))
    si = col.get("SubscriptionId", 1)
    cu = col.get("Currency")
    totals, currency = {}, ""
    for r in rows:
        sid = str(r[si]).lower() if si < len(r) else ""
        totals[sid] = round(float(r[ci] or 0), 2)
        if cu is not None and cu < len(r):
            currency = r[cu]
    return {"totals": totals, "currency": currency}


def subscription_total(subscription_id: str, timeframe: str = "MonthToDate") -> dict:
    """Total actual cost for a subscription over the timeframe (+ currency)."""
    body = {"type": "ActualCost", "timeframe": timeframe,
            "dataset": {"granularity": "None",
                        "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}}}}
    props = _query(subscription_id, body)
    col, rows = _cols(props), props.get("rows", [])
    if not rows:
        return {"cost": 0.0, "currency": ""}
    r = rows[0]
    ci = col.get("Cost", col.get("PreTaxCost", 0))
    cu = col.get("Currency")
    return {"cost": round(float(r[ci] or 0), 2),
            "currency": (r[cu] if cu is not None and cu < len(r) else "")}


def cost_by_dimension(subscription_id: str, dimension: str = "ServiceName",
                      timeframe: str = "MonthToDate", top: int = 12) -> dict:
    """Cost grouped by a dimension (ServiceName / ResourceGroupName), largest first."""
    body = {"type": "ActualCost", "timeframe": timeframe,
            "dataset": {"granularity": "None",
                        "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                        "grouping": [{"type": "Dimension", "name": dimension}]}}
    props = _query(subscription_id, body)
    col, rows = _cols(props), props.get("rows", [])
    ci, ni = col.get("Cost", 0), col.get(dimension, 1)
    cu = col.get("Currency")
    items, currency = [], ""
    for r in rows:
        items.append({"name": str(r[ni]) if ni < len(r) else "(unknown)",
                      "cost": round(float(r[ci] or 0), 2)})
        if cu is not None and cu < len(r):
            currency = r[cu]
    items.sort(key=lambda x: x["cost"], reverse=True)
    if len(items) > top:
        head = items[:top]
        head.append({"name": "Other", "cost": round(sum(x["cost"] for x in items[top:]), 2)})
        items = head
    return {"currency": currency, "items": items}


def cost_daily(subscription_id: str, timeframe: str = "MonthToDate") -> dict:
    """Daily cost trend over the timeframe."""
    body = {"type": "ActualCost", "timeframe": timeframe,
            "dataset": {"granularity": "Daily",
                        "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}}}}
    props = _query(subscription_id, body)
    col, rows = _cols(props), props.get("rows", [])
    ci, di = col.get("Cost", 0), col.get("UsageDate", 1)
    cu = col.get("Currency")
    points, currency = [], ""
    for r in rows:
        raw = str(r[di]) if di < len(r) else ""
        date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 else raw
        points.append({"date": date, "cost": round(float(r[ci] or 0), 2)})
        if cu is not None and cu < len(r):
            currency = r[cu]
    points.sort(key=lambda p: p["date"])
    return {"currency": currency, "points": points}


# Cache computed summaries — spend is month-to-date and barely moves minute to
# minute, so serving a few-minutes-old result spares both pages a re-query (and
# spares the Cost Management throttle). Keyed by timeframe.
_SUMMARY_TTL = 600  # seconds
_summary_lock = threading.Lock()
_summary_cache = {}  # timeframe -> (expires_at, result)


def _compute_summary(timeframe: str) -> dict:
    subs = list_subscriptions()

    # Fast path: one management-group query returns every subscription's total.
    mg = None
    try:
        mg = _mg_totals(timeframe)
    except Exception as exc:
        log.warning("cost: management-group query failed, falling back per-subscription: %s", exc)
        mg = None

    if mg is not None:
        totals, currency = mg["totals"], mg["currency"]
        out = [{**s, "cost": totals.get(s["id"].lower()), "currency": currency} for s in subs]
    else:
        # Fallback: one query per subscription, concurrent, 429-aware.
        def _one(s):
            try:
                t = subscription_total(s["id"], timeframe)
                return {**s, "cost": t["cost"], "currency": t["currency"]}
            except Exception as exc:
                log.error("cost summary for %s failed: %s", s["id"], exc)
                return {**s, "cost": None, "error": str(exc)[:120]}
        if subs:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(subs))) as ex:
                out = list(ex.map(_one, subs))
        else:
            out = []
        currency = next((s.get("currency") for s in out if s.get("currency")), "")

    total = round(sum(s.get("cost") or 0 for s in out), 2)
    out.sort(key=lambda x: (x.get("cost") or 0), reverse=True)
    return {"timeframe": timeframe, "currency": currency, "total": total,
            "subscriptions": out, "as_of": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}


def summary(timeframe: str = "MonthToDate", force: bool = False) -> dict:
    """All subscriptions with their spend for the timeframe (cards + bar), cached."""
    now = time.time()
    with _summary_lock:
        hit = _summary_cache.get(timeframe)
        if hit and not force and now < hit[0]:
            return hit[1]
    result = _compute_summary(timeframe)
    with _summary_lock:
        _summary_cache[timeframe] = (now + _SUMMARY_TTL, result)
    return result
