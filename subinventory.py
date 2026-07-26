"""
Subscription inventory — the manually-owned metadata that Azure doesn't hold:
technical/financial owner, monthly budget, cost centre, environment, etc.

Azure supplies the subscription id/name/state/spend live; this table stores only
the human-owned fields, keyed by subscription id. Raw sqlite via db_backend
(same pattern as audit.py / chats.py).
"""
import logging
from datetime import datetime

import db_backend

log = logging.getLogger(__name__)

# The human-owned fields (everything else is fetched from Azure).
FIELDS = ("technical_owner", "technical_owner_email",
          "financial_owner", "financial_owner_email",
          "budget", "cost_center", "environment", "criticality", "notes")

# Columns managed outside the form-driven upsert (so a card Save never clears
# them). auto_budget_alerts: "on" => this subscription opts into scheduled
# over-budget alert emails to its financial owner.
EXTRA_COLUMNS = ("auto_budget_alerts",)


def _conn():
    return db_backend.connect()


def ensure_table():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscription_inventory (
                subscription_id       TEXT PRIMARY KEY,
                technical_owner       TEXT,
                technical_owner_email TEXT,
                financial_owner       TEXT,
                financial_owner_email TEXT,
                budget                TEXT,
                cost_center           TEXT,
                environment           TEXT,
                criticality           TEXT,
                notes                 TEXT,
                auto_budget_alerts    TEXT,
                updated_by            TEXT,
                updated_ts            TEXT
            )
        """)
        # Add any columns missing from a pre-existing table (e.g. the owner emails
        # or auto_budget_alerts introduced later), so upgrades need no manual migration.
        have = {r["name"] for r in conn.execute("PRAGMA table_info(subscription_inventory)").fetchall()}
        for col in (*FIELDS, *EXTRA_COLUMNS):
            if col not in have:
                conn.execute(f"ALTER TABLE subscription_inventory ADD COLUMN {col} TEXT")


def all_records() -> dict:
    """All stored inventory rows keyed by subscription id."""
    ensure_table()
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM subscription_inventory").fetchall()
    return {r["subscription_id"]: dict(r) for r in rows}


def upsert(subscription_id: str, fields: dict, actor: str = "admin") -> None:
    """Create or update the owner/budget metadata for a subscription."""
    ensure_table()
    sid = str(subscription_id or "").strip()
    if not sid:
        return
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    vals = [str((fields or {}).get(k) or "").strip()[:500] for k in FIELDS]
    with _conn() as conn:
        exists = conn.execute("SELECT 1 FROM subscription_inventory WHERE subscription_id = ?",
                              (sid,)).fetchone()
        if exists:
            sets = ", ".join(f"{k} = ?" for k in FIELDS)
            conn.execute(
                f"UPDATE subscription_inventory SET {sets}, updated_by = ?, updated_ts = ? "
                f"WHERE subscription_id = ?",
                (*vals, actor[:120], ts, sid))
        else:
            cols = ", ".join(["subscription_id", *FIELDS, "updated_by", "updated_ts"])
            ph = ", ".join(["?"] * (len(FIELDS) + 3))
            conn.execute(
                f"INSERT INTO subscription_inventory ({cols}) VALUES ({ph})",
                (sid, *vals, actor[:120], ts))


def set_auto_alerts(subscription_id: str, on: bool, actor: str = "admin") -> None:
    """Toggle scheduled over-budget alerts for one subscription (updates only that
    column, so it never disturbs the owner/budget fields a card Save writes)."""
    ensure_table()
    sid = str(subscription_id or "").strip()
    if not sid:
        return
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    val = "on" if on else ""
    with _conn() as conn:
        exists = conn.execute("SELECT 1 FROM subscription_inventory WHERE subscription_id = ?",
                              (sid,)).fetchone()
        if exists:
            conn.execute("UPDATE subscription_inventory SET auto_budget_alerts = ?, "
                         "updated_by = ?, updated_ts = ? WHERE subscription_id = ?",
                         (val, actor[:120], ts, sid))
        else:
            conn.execute("INSERT INTO subscription_inventory "
                         "(subscription_id, auto_budget_alerts, updated_by, updated_ts) "
                         "VALUES (?, ?, ?, ?)", (sid, val, actor[:120], ts))
