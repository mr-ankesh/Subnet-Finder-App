"""
Persistent agent chats — conversations survive across sessions and devices so
users can reopen their past chats (the old behaviour kept history only in the
ephemeral Flask session).

Raw sqlite3 via db_backend, same pattern as audit.py / changes.py. One row per
conversation; messages are a JSON list of {role, content, ts}.
"""
import json
import logging
from datetime import datetime

import db_backend

log = logging.getLogger(__name__)

KINDS = ("admin", "requester")


def _conn():
    return db_backend.connect()


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def ensure_table():
    with _conn() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS agent_chats (
                id         {db_backend.AUTOINC_PK},
                kind       TEXT NOT NULL,
                owner      TEXT NOT NULL,
                title      TEXT,
                created_ts TEXT,
                updated_ts TEXT,
                messages   TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chats_owner ON agent_chats(kind, owner)")


def create_chat(kind: str, owner: str, title: str = "") -> int:
    ensure_table()
    ts = _now()
    with _conn() as conn:
        return db_backend.insert_returning_id(
            conn,
            "INSERT INTO agent_chats (kind, owner, title, created_ts, updated_ts, messages) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, (owner or "unknown")[:200], (title or "")[:300], ts, ts, json.dumps([])))


def get_chat(cid: int) -> dict:
    ensure_table()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM agent_chats WHERE id = ?", (int(cid),)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["messages"] = json.loads(d.get("messages") or "[]")
    except Exception:
        d["messages"] = []
    return d


def list_chats(kind: str, owner: str, limit: int = 50) -> list:
    """Recent chats for a user (newest first) — id, title, timestamp, msg count."""
    ensure_table()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, title, updated_ts, messages FROM agent_chats "
            "WHERE kind = ? AND owner = ? ORDER BY updated_ts DESC, id DESC LIMIT ?",
            (kind, (owner or "unknown")[:200], max(1, min(int(limit), 200)))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            n = len(json.loads(d.get("messages") or "[]"))
        except Exception:
            n = 0
        out.append({"id": d["id"], "title": d.get("title") or "New chat",
                    "updated_ts": d.get("updated_ts"), "count": n})
    return out


def append_messages(cid: int, new_msgs: list, title_hint: str = None) -> None:
    """Append messages to a chat; set the title from the first user message."""
    ensure_table()
    with _conn() as conn:
        row = conn.execute("SELECT messages, title FROM agent_chats WHERE id = ?",
                           (int(cid),)).fetchone()
        if not row:
            return
        d = dict(row)
        try:
            msgs = json.loads(d.get("messages") or "[]")
        except Exception:
            msgs = []
        stamp = _now()
        for m in new_msgs:
            msgs.append({"role": m.get("role"), "content": m.get("content", ""), "ts": stamp})
        title = d.get("title")
        if (not title) and title_hint:
            title = title_hint.strip().replace("\n", " ")[:80]
        conn.execute("UPDATE agent_chats SET messages = ?, updated_ts = ?, title = ? WHERE id = ?",
                     (json.dumps(msgs), stamp, title, int(cid)))


def owns(cid: int, kind: str, owner: str) -> bool:
    c = get_chat(cid)
    return bool(c and c.get("kind") == kind and c.get("owner") == (owner or "unknown")[:200])


def delete_chat(cid: int, owner: str = None) -> None:
    ensure_table()
    with _conn() as conn:
        if owner is not None:
            conn.execute("DELETE FROM agent_chats WHERE id = ? AND owner = ?",
                         (int(cid), (owner or "unknown")[:200]))
        else:
            conn.execute("DELETE FROM agent_chats WHERE id = ?", (int(cid),))
