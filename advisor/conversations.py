"""
Persistent, resumable advisor conversations — three raw-SQL tables via
db_backend, same pattern as chats.py / advisor/session_store.py.

This is an ADDITIVE layer, not a replacement for session_store.py. The
existing single-shot /api/advisor/chat, /diagram, /prefill and the four
/api/advisor/environment/* routes keep working exactly as before, backed by
advisor_sessions. This module backs the NEW conversation-list-and-resume
flow (advisor/orchestrator.py, the /api/advisor/conversations/* routes).
Migrating the old routes onto this schema is a deliberate follow-up, not
part of this build — see .memory/next-actions.md.

Three tables:
  advisor_conversations — one row per conversation (owner, mode, title,
    status, kb_version, message_count).
  advisor_messages — the full transcript, one row per turn, ordered by a
    per-conversation `seq` (not by row id, so a future re-import/merge
    can't reorder history).
  advisor_state — ONE row per conversation, holding the engine's full
    working state (not just a flat answers map — question_engine/intake
    need pending_followups/resolved_asks/pending_confirm/pending_correction
    too) plus a few denormalised top-level fields for cheap reads, guarded
    by an optimistic-concurrency `version` column.

kb_version is pinned at creation as a single hand-bumped constant (see
KB_VERSION below) — NOT a live aggregate of every KB file's own
`kb_version:` field. catalog_loader's lru_cache means a running process
serves whatever it loaded at start regardless of what's recorded here;
there is no versioned storage of past KB content to actually replay a
conversation against. This field is honestly scoped: it survives a resume
and never gets silently recomputed on read, which is what's actually
achievable and testable — it does not pin runtime behaviour across a KB
edit + process restart.
"""
import json
import logging
from datetime import datetime

import db_backend

log = logging.getLogger(__name__)

KB_VERSION = "1.0.0"  # bump by hand when advisor_kb/ changes meaningfully

VALID_MODES = ("service", "environment")
VALID_STATUSES = ("active", "recommended", "abandoned")
VALID_ROLES = ("user", "assistant", "system")
VALID_MESSAGE_MODES = ("guided", "freeform", "system")


def _conn():
    return db_backend.connect()


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def ensure_tables():
    with _conn() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS advisor_conversations (
                id            {db_backend.AUTOINC_PK},
                owner_key     TEXT NOT NULL,
                title         TEXT,
                mode          TEXT NOT NULL,
                service       TEXT,
                status        TEXT NOT NULL DEFAULT 'active',
                kb_version    TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                created_ts    TEXT,
                updated_ts    TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_advconv_owner "
                     "ON advisor_conversations(owner_key)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS advisor_messages (
                id              """ + db_backend.AUTOINC_PK + """,
                conversation_id INTEGER NOT NULL REFERENCES advisor_conversations(id) ON DELETE CASCADE,
                seq             INTEGER NOT NULL,
                role            TEXT NOT NULL,
                mode            TEXT,
                content         TEXT,
                metadata_json   TEXT,
                created_ts      TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_advmsg_conv "
                     "ON advisor_messages(conversation_id, seq)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS advisor_state (
                conversation_id      INTEGER PRIMARY KEY REFERENCES advisor_conversations(id) ON DELETE CASCADE,
                answers_json         TEXT,
                pending_question_id  TEXT,
                selected_pattern     TEXT,
                recommendation_json  TEXT,
                prefill_payload_json TEXT,
                version              INTEGER NOT NULL DEFAULT 0,
                updated_ts           TEXT
            )
        """)


# ── Conversations ────────────────────────────────────────────────────────

def create_conversation(owner_key: str, mode: str, service: str = None) -> int:
    if mode not in VALID_MODES:
        raise ValueError(f"invalid advisor conversation mode: {mode!r}")
    ensure_tables()
    ts = _now()
    with _conn() as conn:
        cid = db_backend.insert_returning_id(
            conn,
            "INSERT INTO advisor_conversations "
            "(owner_key, title, mode, service, status, kb_version, message_count, "
            "created_ts, updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ((owner_key or "unknown")[:200], "New conversation", mode, service,
             "active", KB_VERSION, 0, ts, ts))
    _ensure_state_row(cid, ts)
    return cid


def _ensure_state_row(conversation_id: int, ts: str = None):
    ts = ts or _now()
    with _conn() as conn:
        row = conn.execute("SELECT conversation_id FROM advisor_state WHERE conversation_id = ?",
                           (int(conversation_id),)).fetchone()
        if row:
            return
        conn.execute(
            "INSERT INTO advisor_state (conversation_id, answers_json, pending_question_id, "
            "selected_pattern, recommendation_json, prefill_payload_json, version, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(conversation_id), json.dumps({}), None, None, None, None, 0, ts))


def get_conversation(conversation_id: int) -> dict:
    ensure_tables()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM advisor_conversations WHERE id = ?",
                           (int(conversation_id),)).fetchone()
    return dict(row) if row else None


def list_conversations(owner_key: str, limit: int = 50) -> list:
    ensure_tables()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, title, mode, service, status, message_count, created_ts, updated_ts "
            "FROM advisor_conversations WHERE owner_key = ? "
            "ORDER BY updated_ts DESC, id DESC LIMIT ?",
            ((owner_key or "unknown")[:200], max(1, min(int(limit), 200)))).fetchall()
    return [dict(r) for r in rows]


def rename_conversation(conversation_id: int, owner_key: str, title: str) -> bool:
    ensure_tables()
    with _conn() as conn:
        res = conn.execute(
            "UPDATE advisor_conversations SET title = ?, updated_ts = ? "
            "WHERE id = ? AND owner_key = ?",
            ((title or "").strip()[:200] or "New conversation", _now(),
             int(conversation_id), (owner_key or "unknown")[:200]))
        return res.rowcount == 1


def delete_conversation(conversation_id: int, owner_key: str) -> bool:
    """Messages and state cascade via ON DELETE CASCADE — nothing orphaned."""
    ensure_tables()
    with _conn() as conn:
        res = conn.execute(
            "DELETE FROM advisor_conversations WHERE id = ? AND owner_key = ?",
            (int(conversation_id), (owner_key or "unknown")[:200]))
        return res.rowcount == 1


def owns(conversation_id: int, owner_key: str) -> bool:
    c = get_conversation(conversation_id)
    return bool(c and c.get("owner_key") == (owner_key or "unknown")[:200])


def _touch(conn, conversation_id: int, ts: str, message_count_delta: int = 0):
    conn.execute(
        "UPDATE advisor_conversations SET updated_ts = ?, "
        "message_count = message_count + ? WHERE id = ?",
        (ts, message_count_delta, int(conversation_id)))


# ── Messages ─────────────────────────────────────────────────────────────

_SUBSTANTIVE_MIN_LEN = 3


def append_message(conversation_id: int, role: str, mode: str, content: str,
                    metadata: dict = None) -> int:
    """Appends one transcript row, auto-incrementing `seq` per conversation.
    Sets the conversation's title from the first substantive user message
    if it's still the creation-time placeholder."""
    if role not in VALID_ROLES:
        raise ValueError(f"invalid advisor message role: {role!r}")
    if mode is not None and mode not in VALID_MESSAGE_MODES:
        raise ValueError(f"invalid advisor message mode: {mode!r}")
    ensure_tables()
    ts = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM advisor_messages WHERE conversation_id = ?",
            (int(conversation_id),)).fetchone()
        seq = int(row["m"]) + 1
        mid = db_backend.insert_returning_id(
            conn,
            "INSERT INTO advisor_messages (conversation_id, seq, role, mode, content, "
            "metadata_json, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(conversation_id), seq, role, mode, content or "",
             json.dumps(metadata) if metadata else None, ts))
        _touch(conn, conversation_id, ts, message_count_delta=1)
        if role == "user" and content and len(content.strip()) >= _SUBSTANTIVE_MIN_LEN:
            conv = conn.execute("SELECT title FROM advisor_conversations WHERE id = ?",
                                (int(conversation_id),)).fetchone()
            if conv and (conv["title"] or "") in ("New conversation", ""):
                title = content.strip().replace("\n", " ")[:80]
                conn.execute("UPDATE advisor_conversations SET title = ? WHERE id = ?",
                             (title, int(conversation_id)))
    return mid


def list_messages(conversation_id: int) -> list:
    ensure_tables()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM advisor_messages WHERE conversation_id = ? ORDER BY seq ASC",
            (int(conversation_id),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d.pop("metadata_json") or "null")
        except Exception:
            d["metadata"] = None
        out.append(d)
    return out


# ── State (optimistic concurrency) ───────────────────────────────────────

def get_state(conversation_id: int) -> dict:
    """Always returns a row (lazily creates a default one) so callers never
    have to special-case "no state yet"."""
    ensure_tables()
    _ensure_state_row(conversation_id)
    with _conn() as conn:
        row = conn.execute("SELECT * FROM advisor_state WHERE conversation_id = ?",
                           (int(conversation_id),)).fetchone()
    d = dict(row)
    try:
        d["answers"] = json.loads(d.pop("answers_json") or "{}")
    except Exception:
        d["answers"] = {}
    for key in ("recommendation_json", "prefill_payload_json"):
        raw = d.pop(key)
        out_key = key[:-len("_json")]
        try:
            d[out_key] = json.loads(raw) if raw else None
        except Exception:
            d[out_key] = None
    return d


_UNSET = object()


def save_state(conversation_id: int, answers: dict, expected_version: int, *,
               pending_question_id=_UNSET, selected_pattern=_UNSET,
               recommendation=_UNSET, prefill_payload=_UNSET) -> bool:
    """Optimistic concurrency: the UPDATE's WHERE clause includes the version
    the caller last read. Returns False (never raises) if another write won
    the race in between — verified identical rowcount semantics on both
    psycopg and sqlite3 for a conditional UPDATE. Callers on False should
    re-fetch via get_state() and surface a conflict rather than retry blindly
    (two tabs open on the same conversation should see this, not silently
    clobber each other).

    `pending_question_id`/`selected_pattern`/`recommendation`/`prefill_payload`
    default to a private _UNSET sentinel, NOT None — every save_state call
    only touches `answers_json` plus whichever of these four the caller
    actually passed. A plain `None` default would mean any call that only
    meant to update the answers (e.g. while detecting a pending correction)
    silently wipes out an already-stored recommendation and pending question
    — a real bug caught while testing the correction flow: storing
    `pending_correction` inside `answers` cleared the just-built
    recommendation because that save_state call didn't re-pass it."""
    ensure_tables()
    _ensure_state_row(conversation_id)
    ts = _now()
    with _conn() as conn:
        current = conn.execute(
            "SELECT pending_question_id, selected_pattern, recommendation_json, "
            "prefill_payload_json FROM advisor_state WHERE conversation_id = ?",
            (int(conversation_id),)).fetchone()
        final_pending_question_id = (current["pending_question_id"]
                                      if pending_question_id is _UNSET else pending_question_id)
        final_selected_pattern = (current["selected_pattern"]
                                   if selected_pattern is _UNSET else selected_pattern)
        final_recommendation_json = (current["recommendation_json"] if recommendation is _UNSET
                                      else (json.dumps(recommendation) if recommendation is not None else None))
        final_prefill_json = (current["prefill_payload_json"] if prefill_payload is _UNSET
                               else (json.dumps(prefill_payload) if prefill_payload is not None else None))
        res = conn.execute(
            "UPDATE advisor_state SET answers_json = ?, pending_question_id = ?, "
            "selected_pattern = ?, recommendation_json = ?, prefill_payload_json = ?, "
            "version = version + 1, updated_ts = ? "
            "WHERE conversation_id = ? AND version = ?",
            (json.dumps(answers or {}), final_pending_question_id, final_selected_pattern,
             final_recommendation_json, final_prefill_json,
             ts, int(conversation_id), int(expected_version)))
        return res.rowcount == 1


def set_status(conversation_id: int, status: str):
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid advisor conversation status: {status!r}")
    ensure_tables()
    with _conn() as conn:
        conn.execute("UPDATE advisor_conversations SET status = ?, updated_ts = ? WHERE id = ?",
                     (status, _now(), int(conversation_id)))


def set_service(conversation_id: int, service: str):
    ensure_tables()
    with _conn() as conn:
        conn.execute("UPDATE advisor_conversations SET service = ?, updated_ts = ? WHERE id = ?",
                     (service, _now(), int(conversation_id)))
