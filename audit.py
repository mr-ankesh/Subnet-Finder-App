"""
Audit trail — durable record of who did what, when, on which request.

Raw sqlite3 (same pattern as db_utils.py) so agents and routes can write
without Flask-SQLAlchemy session scoping. Writes are best-effort: an audit
failure must never break the operation being audited.
"""
import json
import logging
import os
import sqlite3
from datetime import datetime

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "data", "requests.db")


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_table():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT NOT NULL,
                actor      TEXT NOT NULL,
                actor_role TEXT NOT NULL DEFAULT 'system',
                action     TEXT NOT NULL,
                request_id INTEGER,
                summary    TEXT,
                data       TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_log(request_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)")


def record(action: str, actor: str = "system", actor_role: str = "system",
           request_id=None, summary: str = "", data: dict = None):
    """Append an audit entry. Never raises — auditing must not break operations."""
    try:
        ensure_table()
        with _conn() as conn:
            conn.execute(
                """INSERT INTO audit_log (ts, actor, actor_role, action, request_id, summary, data)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                 (actor or "system")[:200], actor_role, action,
                 int(request_id) if request_id is not None else None,
                 (summary or "")[:500],
                 json.dumps(data) if data else None),
            )
    except Exception as exc:
        log.error("audit.record failed (%s): %s", action, exc)


def list_entries(request_id=None, actor: str = None, action: str = None,
                 q: str = None, limit: int = 200) -> list:
    """Latest-first audit entries with optional filters."""
    try:
        ensure_table()
        where, params = [], []
        if request_id is not None:
            where.append("request_id = ?");            params.append(int(request_id))
        if actor:
            where.append("LOWER(actor) LIKE ?");        params.append(f"%{actor.lower()}%")
        if action:
            where.append("action = ?");                 params.append(action)
        if q:
            where.append("(LOWER(summary) LIKE ? OR LOWER(actor) LIKE ? OR LOWER(action) LIKE ?)")
            params += [f"%{q.lower()}%"] * 3
        sql = "SELECT * FROM audit_log"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with _conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["data"] = json.loads(d["data"]) if d["data"] else {}
            except Exception:
                d["data"] = {}
            out.append(d)
        return out
    except Exception as exc:
        log.error("audit.list_entries failed: %s", exc)
        return []


def distinct_actions() -> list:
    """Distinct action slugs, for the filter dropdown."""
    try:
        ensure_table()
        with _conn() as conn:
            rows = conn.execute("SELECT DISTINCT action FROM audit_log ORDER BY action").fetchall()
        return [r["action"] for r in rows]
    except Exception:
        return []
