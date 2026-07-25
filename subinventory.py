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
FIELDS = ("technical_owner", "financial_owner", "budget", "cost_center",
          "environment", "criticality", "notes")


def _conn():
    return db_backend.connect()


def ensure_table():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscription_inventory (
                subscription_id TEXT PRIMARY KEY,
                technical_owner TEXT,
                financial_owner TEXT,
                budget          TEXT,
                cost_center     TEXT,
                environment     TEXT,
                criticality     TEXT,
                notes           TEXT,
                updated_by      TEXT,
                updated_ts      TEXT
            )
        """)


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
