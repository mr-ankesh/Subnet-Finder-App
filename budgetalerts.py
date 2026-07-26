"""
Automatic over-budget alerts for subscriptions.

The hard part isn't emailing at 70/80/90% — it's NOT crying wolf. Raw "% of
budget" is misleading near month-end: 90% spent with two days left is usually
fine, because at the current pace you'll still finish under budget.

The fix is a run-rate forecast used as an intelligent GATE on the raw thresholds:

    elapsed        = day_of_month / days_in_month
    raw_pct        = spend / budget * 100
    projected_pct  = raw_pct / elapsed        (linear month-end forecast)

Decision (per subscription that opted in, checked periodically):
  1. A raw threshold (70 notify / 80 warning / 90 critical) is the NECESSARY
     trigger -> candidate severity.
  2. Intelligent suppression: if not already over budget now AND the forecast
     lands under budget (projected_pct < PROJECTION_GATE), suppress the email —
     you're pacing fine even at a scary-looking raw percentage.
  3. If already >=100% now -> always critical (pace can't save you).
  4. The forecast only REMOVES false alarms; it never invents new ones.
  5. Dedup: email only when severity escalates within a month, so an owner is
     never spammed daily for the same (or a lower) severity.

Storage: raw sqlite via db_backend, same pattern as subinventory / audit.
"""
import calendar
import logging
import threading
import time
from datetime import datetime

import db_backend
from config import cfg

log = logging.getLogger(__name__)

# Raw-percentage thresholds, most-severe first.
THRESHOLDS = [(90, "critical"), (80, "warning"), (70, "notify")]
_RANK = {"notify": 1, "warning": 2, "critical": 3}
# Suppress a threshold email if the forecast finishes below this % of budget.
PROJECTION_GATE = 100


def _conn():
    return db_backend.connect()


def ensure_table():
    """Tracks the highest severity already emailed per subscription per month, so
    escalations notify once and don't repeat every scheduler run."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS budget_alert_state (
                subscription_id TEXT NOT NULL,
                period          TEXT NOT NULL,   -- 'YYYY-MM'
                severity        TEXT,            -- highest severity emailed this period
                updated_ts      TEXT,
                PRIMARY KEY (subscription_id, period)
            )
        """)


def _to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def assess(sub: dict, now: datetime = None) -> dict:
    """Pure decision function for one subscription. Returns a dict describing the
    budget position and whether an alert is warranted:

      {ok, budget, spend, raw_pct, elapsed_pct, projected_spend, projected_pct,
       days_left, days_in_month, on_pace, severity, actionable, reason}

    `severity` is the candidate band (or None). `actionable` is True only when an
    email should go out (threshold crossed AND not suppressed by the forecast).
    """
    now = now or datetime.utcnow()
    inv = sub.get("inventory", {}) or {}
    budget = _to_float(inv.get("budget"))
    spend = sub.get("spend")
    if budget is None or budget == 0 or spend is None:
        return {"ok": False, "severity": None, "actionable": False,
                "reason": "no budget or spend"}

    days_in_month = calendar.monthrange(now.year, now.month)[1]
    day = now.day
    elapsed = max(day / days_in_month, 1.0 / days_in_month)  # avoid div-by-zero on day 0
    raw_pct = round(spend / budget * 100, 1)
    projected_spend = round(spend / elapsed, 2)
    projected_pct = round(projected_spend / budget * 100, 1)
    days_left = days_in_month - day
    over_now = raw_pct >= 100

    # Candidate severity from the raw thresholds.
    severity = None
    for at, sev in THRESHOLDS:
        if raw_pct >= at:
            severity = sev
            break

    actionable, reason = False, ""
    if severity is None:
        reason = f"{raw_pct}% — below the 70% notice threshold"
    elif over_now:
        actionable, reason = True, "already over budget this month"
    elif projected_pct < PROJECTION_GATE:
        # The intelligent gate: high raw %, but pacing to finish under budget.
        reason = (f"{raw_pct}% used but on pace to finish at ~{projected_pct}% "
                  f"({days_left} day(s) left) — under budget, suppressed")
    else:
        actionable, reason = True, (f"projected ~{projected_pct}% by month-end "
                                    f"({days_left} day(s) left) — trending over budget")

    return {"ok": True, "budget": budget, "spend": spend, "raw_pct": raw_pct,
            "elapsed_pct": round(elapsed * 100, 1), "projected_spend": projected_spend,
            "projected_pct": projected_pct, "days_left": days_left,
            "days_in_month": days_in_month, "on_pace": projected_pct < PROJECTION_GATE,
            "severity": severity, "actionable": actionable, "reason": reason}


def _last_severity(sid: str, period: str) -> str | None:
    with _conn() as conn:
        row = conn.execute("SELECT severity FROM budget_alert_state WHERE "
                           "subscription_id = ? AND period = ?", (sid, period)).fetchone()
    return row["severity"] if row else None


def _record_severity(sid: str, period: str, severity: str) -> None:
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    with _conn() as conn:
        exists = conn.execute("SELECT 1 FROM budget_alert_state WHERE "
                              "subscription_id = ? AND period = ?", (sid, period)).fetchone()
        if exists:
            conn.execute("UPDATE budget_alert_state SET severity = ?, updated_ts = ? "
                         "WHERE subscription_id = ? AND period = ?", (severity, ts, sid, period))
        else:
            conn.execute("INSERT INTO budget_alert_state (subscription_id, period, severity, "
                         "updated_ts) VALUES (?, ?, ?, ?)", (sid, period, severity, ts))


def evaluate_and_send(dry_run: bool = False, force: bool = False) -> dict:
    """Check every opted-in subscription and email escalations. Returns a report:
      {checked, sent, suppressed, skipped, actions:[...]}.

    dry_run: assess + decide but never send or record (for the preview/test button).
    force:   ignore the per-month dedup (re-send even if already notified).
    """
    ensure_table()
    import subinventory, costmgmt, notifications
    currency = cfg.COST_CURRENCY or "$"
    now = datetime.utcnow()
    period = now.strftime("%Y-%m")

    stored = subinventory.all_records()
    opted_in = {sid: inv for sid, inv in stored.items()
                if (inv.get("auto_budget_alerts") == "on") and inv.get("budget")}

    report = {"checked": 0, "sent": 0, "suppressed": 0, "skipped": 0,
              "period": period, "actions": []}
    if not opted_in:
        return report

    # One spend lookup for all subscriptions (cached).
    spend_map, names = {}, {}
    if costmgmt.configured():
        try:
            for x in costmgmt.summary("MonthToDate").get("subscriptions", []):
                spend_map[x["id"]] = x.get("cost")
                names[x["id"]] = x.get("name", x["id"])
        except Exception:
            log.exception("budget alerts: spend lookup failed")

    for sid, inv in opted_in.items():
        report["checked"] += 1
        sub = {"id": sid, "name": names.get(sid, sid), "spend": spend_map.get(sid),
               "inventory": inv}
        a = assess(sub, now)
        action = {"id": sid, "name": sub["name"], "raw_pct": a.get("raw_pct"),
                  "projected_pct": a.get("projected_pct"), "severity": a.get("severity"),
                  "reason": a.get("reason")}

        if not a["ok"] or not a["actionable"]:
            report["suppressed" if (a.get("severity") and not a["actionable"]) else "skipped"] += 1
            action["result"] = "suppressed" if a.get("severity") else "no-op"
            report["actions"].append(action)
            continue

        # Dedup: only escalate — skip if we already emailed this severity or higher.
        last = None if force else _last_severity(sid, period)
        if last and _RANK.get(a["severity"], 0) <= _RANK.get(last, 0):
            action["result"] = f"already-notified ({last})"
            report["skipped"] += 1
            report["actions"].append(action)
            continue

        if dry_run:
            action["result"] = f"would-send ({a['severity']})"
            action["to"] = (inv.get("financial_owner_email") or "").strip() or "(no owner email)"
            report["sent"] += 1
            report["actions"].append(action)
            continue

        res = notifications.send_threshold_alert(sub, a, currency)
        if res.get("ok"):
            _record_severity(sid, period, a["severity"])
            report["sent"] += 1
            action["result"] = f"sent ({a['severity']}) → {res.get('to')}"
        else:
            report["skipped"] += 1
            action["result"] = f"not sent: {res.get('message')}"
        report["actions"].append(action)

    log.info("budget alerts %s: checked=%d sent=%d suppressed=%d skipped=%d",
             "(dry-run)" if dry_run else "", report["checked"], report["sent"],
             report["suppressed"], report["skipped"])
    return report


# ── Background scheduler ──────────────────────────────────────────────────
_scheduler_started = False


def start_scheduler(app) -> None:
    """Start the periodic budget checker as a daemon thread (idempotent). Runs
    only when BUDGET_ALERTS_ENABLED; the interval is BUDGET_ALERT_INTERVAL_HOURS."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _loop():
        # Small initial delay so startup isn't slowed and settings are loaded.
        time.sleep(60)
        while True:
            try:
                if cfg.BUDGET_ALERTS_ENABLED:
                    with app.app_context():
                        evaluate_and_send()
            except Exception:
                log.exception("budget alert scheduler tick failed")
            hours = cfg.BUDGET_ALERT_INTERVAL_HOURS or 24
            time.sleep(max(1, int(hours)) * 3600)

    threading.Thread(target=_loop, daemon=True, name="budget-alerts").start()
    log.info("budget alert scheduler started (enabled=%s, every %sh)",
             cfg.BUDGET_ALERTS_ENABLED, cfg.BUDGET_ALERT_INTERVAL_HOURS)
