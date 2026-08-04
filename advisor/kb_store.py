"""
DB-override storage for the AI Architecture Advisor knowledge base
(advisor_kb/), following settings_store.py's pattern — DB override, file
default — applied to whole files instead of scalar values.

CRITICAL: advisor_kb/ is baked into the container image. Prod runs 3
replicas and deploys by rebuilding that image. Writing an uploaded KB to
disk would not persist across restarts and would diverge between pods, so
uploaded KB content lives in the DB, never on disk.

Two tables:
  advisor_kb_versions — one row per activation (upload confirmed, or a
    revert, which is itself a new activation — see revert_to()). Exactly one
    row is ever status='active', enforced in code, not a DB constraint.
  advisor_kb_files — the file content for one version.

get_active_version() is TTL-cached (same shape as settings_store's
_cache/_cache_at) so every replica converges on a new activation within one
TTL window, not at next restart. catalog_loader.py is the only other module
that should import this one; nothing else needs direct DB-KB access.
"""
import hashlib
import json
import logging
import threading
import time
from datetime import datetime

import audit
import changes
import db_backend

log = logging.getLogger(__name__)

_CACHE_TTL = 5.0  # seconds — matches settings_store.py's multi-replica convergence window

_lock = threading.Lock()
_cache_active: dict = None
_cache_at: float = 0.0
_cache_populated = False

VALID_STATUSES = ("active", "superseded", "reverted")


def _conn():
    return db_backend.connect()


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def ensure_tables():
    with _conn() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS advisor_kb_versions (
                id                      {db_backend.AUTOINC_PK},
                version_label           TEXT NOT NULL,
                uploaded_by             TEXT NOT NULL,
                uploaded_at             TEXT NOT NULL,
                status                  TEXT NOT NULL,
                notes                   TEXT,
                file_count              INTEGER NOT NULL,
                kb_version              TEXT NOT NULL,
                validation_report_json  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS advisor_kb_files (
                id            """ + db_backend.AUTOINC_PK + """,
                version_id    INTEGER NOT NULL REFERENCES advisor_kb_versions(id) ON DELETE CASCADE,
                relative_path TEXT NOT NULL,
                content       TEXT NOT NULL,
                sha256        TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_advkbfiles_version "
                     "ON advisor_kb_files(version_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_advkbversions_status "
                     "ON advisor_kb_versions(status)")


def invalidate_cache():
    global _cache_at, _cache_populated
    with _lock:
        _cache_at = 0.0
        _cache_populated = False


def _load_active() -> dict:
    ensure_tables()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM advisor_kb_versions WHERE status = 'active' "
            "ORDER BY id DESC LIMIT 1").fetchone()
    return row


def get_active_version() -> dict:
    """The currently active version row, or None if no KB has ever been
    uploaded (callers should fall back to disk). TTL-cached so a DB read
    isn't on the hot path of every catalog_loader call, and so all replicas
    observe an activation within _CACHE_TTL seconds of each other."""
    global _cache_active, _cache_at, _cache_populated
    now = time.monotonic()
    with _lock:
        if not _cache_populated or (now - _cache_at) > _CACHE_TTL:
            _cache_active = _load_active()
            _cache_at = now
            _cache_populated = True
        return dict(_cache_active) if _cache_active else None


def get_files(version_id: int) -> dict:
    """{relative_path: content} for one version."""
    ensure_tables()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT relative_path, content FROM advisor_kb_files WHERE version_id = ?",
            (int(version_id),)).fetchall()
    return {r["relative_path"]: r["content"] for r in rows}


def list_versions() -> list:
    ensure_tables()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, version_label, uploaded_by, uploaded_at, status, notes, "
            "file_count, kb_version FROM advisor_kb_versions ORDER BY id DESC").fetchall()
    return rows


def get_version(version_id: int) -> dict:
    ensure_tables()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM advisor_kb_versions WHERE id = ?",
                           (int(version_id),)).fetchone()
    return row


def _next_version_label(conn) -> str:
    """Takes the caller's already-open connection/transaction — never opens
    its own. activate()/revert_to() call this from inside a transaction that
    already holds a row-exclusive lock on advisor_kb_versions (the
    'supersede the current active row' UPDATE); a second connection trying
    to ensure_tables()/CREATE INDEX against the same table while that's
    uncommitted deadlocks against the caller's own connection on Postgres —
    a real hang caught only by testing against real Postgres, not SQLite
    (whose WAL-mode MVCC let a second connection's SELECT through, masking
    it)."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT version_label FROM advisor_kb_versions WHERE version_label LIKE ?",
        (f"{today}-%",)).fetchall()
    seqs = []
    for r in rows:
        try:
            seqs.append(int(r["version_label"].rsplit("-", 1)[-1]))
        except (ValueError, IndexError):
            pass
    return f"{today}-{(max(seqs) + 1) if seqs else 1}"


def _insert_version(conn, files: dict, uploaded_by: str, notes: str,
                     validation_report: dict, kb_version_label: str, status: str,
                     version_label: str = None) -> int:
    ts = _now()
    label = version_label or _next_version_label(conn)
    version_id = db_backend.insert_returning_id(
        conn,
        "INSERT INTO advisor_kb_versions (version_label, uploaded_by, uploaded_at, "
        "status, notes, file_count, kb_version, validation_report_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (label, uploaded_by, ts, status, notes or "", len(files),
         kb_version_label, json.dumps(validation_report or {})))
    for relative_path, content in files.items():
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT INTO advisor_kb_files (version_id, relative_path, content, sha256) "
            "VALUES (?, ?, ?, ?)",
            (version_id, relative_path, content, sha))
    return version_id


def activate(files: dict, uploaded_by: str, notes: str, validation_report: dict,
             kb_version_label: str) -> int:
    """One transaction: supersede whatever's currently active, insert the new
    version as active. 'Every activation creates a new version row; the
    previous becomes superseded.'"""
    ensure_tables()
    with _conn() as conn:
        conn.execute(
            "UPDATE advisor_kb_versions SET status = 'superseded' WHERE status = 'active'")
        version_id = _insert_version(conn, files, uploaded_by, notes,
                                      validation_report, kb_version_label, "active")
    invalidate_cache()
    return version_id


def _audit_activation(action: str, actor: str, before_label, after_label: str,
                       target_version_id: int, revert_params: dict, diff_summary: str,
                       validation_report: dict):
    """Dual-write to the change ledger and audit trail — every KB activation
    or revert goes through this, never optional, never route-dependent.
    'Treat this as more sensitive than a firewall rule — it changes what the
    portal tells people to build.' Both changes.record()/audit.record()
    never raise on their own, so a logging failure can't block an
    activation that already succeeded in the DB."""
    changes.record(
        action=action, actor=actor, target=f"advisor_kb_version:{target_version_id}",
        summary=f"{before_label or '(none)'} -> {after_label}",
        before=before_label, after=after_label,
        revert_op="advisor_kb_revert", revert_params=revert_params)
    audit.record(
        action=action, actor=actor, actor_role="superadmin",
        summary=f"Advisor KB version {after_label} activated (was {before_label or 'none'})",
        data={"diff_summary": diff_summary, "validation_report": validation_report})


def activate_and_audit(files: dict, uploaded_by: str, notes: str, validation_report: dict,
                        kb_version_label: str, diff_summary: str = "") -> int:
    """The audited entry point for a normal upload activation — routes call
    this, never the bare activate() above, so the audit/change-ledger
    dual-write can't be forgotten by a future caller."""
    previous = get_active_version()
    version_id = activate(files, uploaded_by, notes, validation_report, kb_version_label)
    new_version = get_version(version_id)
    _audit_activation(
        "advisor_kb_activate", uploaded_by,
        previous["version_label"] if previous else None, new_version["version_label"],
        version_id, {"version_id": previous["id"]} if previous else None,
        diff_summary, validation_report)
    return version_id


def revert_to(version_id: int, actor: str, notes: str = "") -> int:
    """Revert is itself a new activation: copies the target version's files
    into a brand-new version row, which becomes active. The version being
    reverted TO is untouched (still 'superseded'); nothing is marked
    'reverted' here — 'reverted' status is reserved for a version that was
    itself superseded by a revert away from it (set below)."""
    target = get_version(version_id)
    if target is None:
        raise ValueError(f"unknown advisor KB version id {version_id}")
    files = get_files(version_id)
    ensure_tables()
    with _conn() as conn:
        current_active = conn.execute(
            "SELECT id FROM advisor_kb_versions WHERE status = 'active'").fetchone()
        if current_active:
            conn.execute("UPDATE advisor_kb_versions SET status = 'reverted' WHERE id = ?",
                         (current_active["id"],))
        new_notes = notes or f"Reverted to {target['version_label']}"
        new_id = _insert_version(conn, files, actor, new_notes,
                                  json.loads(target["validation_report_json"] or "{}"),
                                  target["kb_version"], "active")
    invalidate_cache()
    return new_id


def revert_and_audit(version_id: int, actor: str, notes: str = "") -> int:
    """The audited entry point for a revert — routes call this, never the
    bare revert_to() above, for the same reason activate_and_audit() exists.
    Itself recorded as a new activation in the change ledger, per spec:
    'One-click revert to any prior version, itself recorded as a new
    activation.'"""
    previous = get_active_version()
    target = get_version(version_id)
    if target is None:
        raise ValueError(f"unknown advisor KB version id {version_id}")
    new_id = revert_to(version_id, actor, notes)
    new_version = get_version(new_id)
    _audit_activation(
        "advisor_kb_revert", actor,
        previous["version_label"] if previous else None, new_version["version_label"],
        new_id, {"version_id": previous["id"]} if previous else None,
        f"Reverted to content from version {target['version_label']}",
        json.loads(target["validation_report_json"] or "{}"))
    return new_id
