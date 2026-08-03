"""
Persistent advisor conversation state — raw sqlite3/db_backend, same pattern
as chats.py. One row per advisor conversation.

Deliberately NOT chats.py's agent_chats table: an advisor session is
structured state (answers-so-far, current question id, derived values,
selected pattern, escalation flags), not an append-only message transcript,
so bolting it onto agent_chats.messages would conflate two different shapes
in one column. `state` here is a single JSON blob (mirrors chats.py's own
`messages` column) since the advisor process is single-threaded per
conversation and the whole state is read/written together.

No module-level cache — prod runs 3 replicas; state must survive being
served by a different pod on the next request (see CLAUDE.md's
local-vs-prod table).
"""
import json
import logging
from datetime import datetime

import db_backend

log = logging.getLogger(__name__)

KB_VERSION = "1.0.0"


def _conn():
    return db_backend.connect()


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def ensure_table():
    with _conn() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS advisor_sessions (
                id               {db_backend.AUTOINC_PK},
                owner            TEXT NOT NULL,
                kb_version       TEXT,
                state            TEXT,
                prefill_payload  TEXT,
                created_ts       TEXT,
                updated_ts       TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_advisor_sessions_owner "
                     "ON advisor_sessions(owner)")


def create_session(owner: str) -> int:
    ensure_table()
    ts = _now()
    initial_state = {"answers": {}, "derived": {}, "escalations": [], "warnings": [],
                      "deviations": [], "selected_pattern": None, "blocked": False,
                      "blocker_message": None, "current_question_id": None}
    with _conn() as conn:
        return db_backend.insert_returning_id(
            conn,
            "INSERT INTO advisor_sessions (owner, kb_version, state, prefill_payload, "
            "created_ts, updated_ts) VALUES (?, ?, ?, ?, ?, ?)",
            ((owner or "unknown")[:200], KB_VERSION, json.dumps(initial_state), None, ts, ts))


def get_session(session_id: int) -> dict:
    ensure_table()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM advisor_sessions WHERE id = ?",
                           (int(session_id),)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["state"] = json.loads(d.get("state") or "{}")
    except Exception:
        d["state"] = {}
    try:
        d["prefill_payload"] = json.loads(d["prefill_payload"]) if d.get("prefill_payload") else None
    except Exception:
        d["prefill_payload"] = None
    return d


def save_state(session_id: int, state: dict) -> None:
    ensure_table()
    with _conn() as conn:
        conn.execute("UPDATE advisor_sessions SET state = ?, updated_ts = ? WHERE id = ?",
                     (json.dumps(state), _now(), int(session_id)))


def save_prefill(session_id: int, payload: dict) -> None:
    ensure_table()
    with _conn() as conn:
        conn.execute("UPDATE advisor_sessions SET prefill_payload = ?, updated_ts = ? WHERE id = ?",
                     (json.dumps(payload), _now(), int(session_id)))


def owns(session_id: int, owner: str) -> bool:
    s = get_session(session_id)
    return bool(s and s.get("owner") == (owner or "unknown")[:200])
