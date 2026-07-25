"""
Subscription cost dashboard — uses a SEPARATE service principal (COST_* settings)
isolated from the network-automation credentials. Talks to the Azure Cost
Management + Resource Manager REST APIs directly with a client-credential token.

Read-only. The cost SP needs, on each reported scope:
  * Cost Management Reader  (to run cost queries)
  * Reader                  (to list subscriptions)
"""
import logging
from datetime import datetime

import requests
from config import cfg

log = logging.getLogger(__name__)

_ARM = "https://management.azure.com"
_QUERY_API = "2023-11-01"
_SUBS_API = "2022-12-01"


def configured() -> bool:
    return bool(cfg.COST_TENANT_ID and cfg.COST_CLIENT_ID and cfg.COST_CLIENT_SECRET)


def _token() -> str:
    from azure.identity import ClientSecretCredential
    cred = ClientSecretCredential(cfg.COST_TENANT_ID, cfg.COST_CLIENT_ID, cfg.COST_CLIENT_SECRET)
    return cred.get_token(f"{_ARM}/.default").token


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


def _query(subscription_id: str, body: dict) -> dict:
    url = (f"{_ARM}/subscriptions/{subscription_id}"
           f"/providers/Microsoft.CostManagement/query?api-version={_QUERY_API}")
    resp = requests.post(url, headers=_headers(), json=body, timeout=45)
    resp.raise_for_status()
    return resp.json().get("properties", {})


def _cols(props):
    return {c.get("name"): i for i, c in enumerate(props.get("columns", []))}


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


def summary(timeframe: str = "MonthToDate") -> dict:
    """All subscriptions with their spend for the timeframe (for the cards + bar)."""
    subs = list_subscriptions()
    out, total, currency = [], 0.0, ""
    for s in subs:
        try:
            t = subscription_total(s["id"], timeframe)
            s = {**s, "cost": t["cost"], "currency": t["currency"]}
            total += t["cost"]
            currency = currency or t["currency"]
        except Exception as exc:
            log.error("cost summary for %s failed: %s", s["id"], exc)
            s = {**s, "cost": None, "error": str(exc)[:120]}
        out.append(s)
    out.sort(key=lambda x: (x.get("cost") or 0), reverse=True)
    return {"timeframe": timeframe, "currency": currency, "total": round(total, 2),
            "subscriptions": out, "as_of": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}
