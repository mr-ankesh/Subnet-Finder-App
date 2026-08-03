"""
Backend-agnostic database access for the raw-SQL modules (db_utils, audit,
settings_store, changes, search).

Single writer or many: when DATABASE_URL points at Postgres, the app can run
multiple replicas; otherwise it falls back to the bundled SQLite file (single
replica). The ORM (models.py via Flask-SQLAlchemy) is already portable — this
module gives the raw-SQL code the same portability with a thin wrapper that:

  • translates '?' placeholders to '%s' on Postgres,
  • no-ops SQLite PRAGMAs on Postgres,
  • returns plain dict rows on both (datetimes normalised to strings, so
    downstream code sees the same shape it always did under SQLite),
  • commits/rolls back/closes on the `with` block,
  • provides insert_returning_id() for the two AUTOINCREMENT insert sites.

No app imports here (config.py imports settings_store which imports this),
so it must stay dependency-free beyond the DB drivers.
"""
import datetime as _dt
import logging
import os

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(_HERE, "data", "requests.db")


def _database_url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip()


IS_POSTGRES = _database_url().startswith(("postgres://", "postgresql://"))

# Portable primary-key DDL for the raw-created tables (change_log, and the
# best-effort re-creates in audit/settings).
AUTOINC_PK = "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"


def sqlalchemy_uri() -> str:
    """URI for Flask-SQLAlchemy — Postgres (psycopg3 driver) or the SQLite file."""
    url = _database_url()
    if url:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://") and "+psycopg" not in url:
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
    return f"sqlite:///{SQLITE_PATH}"


def safe_uri() -> str:
    """URI with any password masked — for /health output."""
    uri = sqlalchemy_uri()
    if "@" in uri and "//" in uri:
        head, tail = uri.split("//", 1)
        if "@" in tail:
            creds, host = tail.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{head}//{user}:***@{host}"
    return uri


def _norm(value):
    """Normalise a cell so raw callers see the SQLite-era shape (str timestamps)."""
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, _dt.datetime) else str(value)
    return value


class _Result:
    """Eager, backend-neutral result: dict rows materialised at fetch time."""
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = getattr(cursor, "lastrowid", None)

    def _to_dict(self, row):
        if row is None:
            return None
        if isinstance(row, dict):
            return {k: _norm(v) for k, v in row.items()}
        # sqlite3.Row supports mapping via .keys()
        return {k: _norm(row[k]) for k in row.keys()}

    def fetchone(self):
        return self._to_dict(self._cursor.fetchone())

    def fetchall(self):
        return [self._to_dict(r) for r in self._cursor.fetchall()]

    @property
    def rowcount(self):
        """Rows affected by the last UPDATE/DELETE — verified identical on both
        psycopg and sqlite3 for a conditional UPDATE (1 on match, 0 on a stale
        WHERE clause), which is what optimistic-concurrency writers rely on."""
        return getattr(self._cursor, "rowcount", -1)


class _Conn:
    """Wraps a raw sqlite3 / psycopg connection with a uniform surface."""
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        if IS_POSTGRES:
            if sql.lstrip()[:6].upper() == "PRAGMA":
                return _Result(_EmptyCursor())
            sql = sql.replace("?", "%s")
        cur = self._raw.cursor()
        if params:
            cur.execute(sql, tuple(params))
        else:
            cur.execute(sql)
        return _Result(cur)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self.close()


class _EmptyCursor:
    lastrowid = None
    def fetchone(self): return None
    def fetchall(self): return []


def connect():
    """A wrapped connection. `with connect() as conn: conn.execute(...)`."""
    if IS_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row
        raw = psycopg.connect(_database_url(), row_factory=dict_row, connect_timeout=10)
        return _Conn(raw)
    import sqlite3
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
    raw = sqlite3.connect(SQLITE_PATH, timeout=10)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=ON")
    return _Conn(raw)


def insert_returning_id(conn, sql, params) -> int:
    """INSERT that returns the new row id on both backends."""
    if IS_POSTGRES:
        res = conn.execute(sql.rstrip().rstrip(";") + " RETURNING id", params)
        row = res.fetchone()
        return row["id"] if row else None
    return conn.execute(sql, params).lastrowid
