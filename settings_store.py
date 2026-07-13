"""
DB-backed settings overrides — key/value store over the app_settings table.

Uses raw sqlite3 (same pattern as db_utils.py) so it works without a Flask
app context and can be imported by config.py without circular imports.

Resolution order lives in config.py: DB override → env var → code default.
Secret values (client secrets, SMTP passwords) are encrypted at rest with
Fernet; the key is derived from FLASK_SECRET_KEY, so changing that env var
invalidates stored secrets (they must be re-entered in the settings UI).
"""
import base64
import hashlib
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "data", "requests.db")

_CACHE_TTL = 5.0  # seconds — keeps multi-worker deployments reasonably fresh

_lock = threading.Lock()
_cache: dict = {}
_cache_at: float = 0.0


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_table():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                is_secret  INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            )
        """)


# ── Encryption (secrets at rest) ────────────────────────────────────────────

def _fernet():
    from cryptography.fernet import Fernet
    secret = os.environ.get("FLASK_SECRET_KEY", "change-me-in-production")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def using_default_secret_key() -> bool:
    """True when FLASK_SECRET_KEY is unset/default — secrets are weakly protected."""
    return os.environ.get("FLASK_SECRET_KEY", "change-me-in-production") == "change-me-in-production"


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(token: str):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:
        log.warning("Could not decrypt a stored secret (FLASK_SECRET_KEY changed?) — treating as unset.")
        return None


# ── Public API ──────────────────────────────────────────────────────────────

def _load_all() -> dict:
    """Return {key: decrypted_value} for every override row."""
    try:
        ensure_table()
        with _conn() as conn:
            rows = conn.execute("SELECT key, value, is_secret FROM app_settings").fetchall()
    except Exception as exc:
        log.error("settings_store read failed: %s", exc)
        return {}
    out = {}
    for r in rows:
        val = r["value"]
        if r["is_secret"] and val is not None:
            val = _decrypt(val)
        if val is not None:
            out[r["key"]] = val
    return out


def all_overrides() -> dict:
    """Cached view of all overrides (decrypted). TTL-based refresh."""
    global _cache, _cache_at
    now = time.monotonic()
    with _lock:
        if now - _cache_at > _CACHE_TTL:
            _cache = _load_all()
            _cache_at = now
        return dict(_cache)


def invalidate_cache():
    global _cache_at
    with _lock:
        _cache_at = 0.0


def get_override(key: str):
    return all_overrides().get(key)


def set_override(key: str, value: str, is_secret: bool = False):
    stored = _encrypt(value) if is_secret else value
    ensure_table()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO app_settings (key, value, is_secret, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value, is_secret=excluded.is_secret, updated_at=excluded.updated_at""",
            (key, stored, 1 if is_secret else 0, datetime.utcnow().isoformat(timespec="seconds")),
        )
    invalidate_cache()


def delete_override(key: str):
    ensure_table()
    with _conn() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    invalidate_cache()
