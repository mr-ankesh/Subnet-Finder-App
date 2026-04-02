"""
Direct SQLite3 helpers for agent tool DB operations.
Bypasses Flask-SQLAlchemy's request-scoped session entirely to avoid
nested-context / session-scope issues when agents write from within a
Flask request handler.
All functions read/write the same requests.db file that Flask-SQLAlchemy uses.
"""
import json
import logging
import os
import sqlite3
from datetime import datetime

from models import RequestStatus

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "data", "requests.db")


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class RequestProxy:
    """Thin wrapper around a sqlite3.Row dict so notifications.py can call req.field."""
    def __init__(self, row: dict):
        self.__dict__.update(row)
        # Ensure datetime fields are objects (SQLite returns strings)
        for attr in ("created_at", "updated_at"):
            val = getattr(self, attr, None)
            if isinstance(val, str) and val:
                try:
                    object.__setattr__(self, attr, datetime.strptime(val[:19], "%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    pass
        self.hub_integration = bool(row.get("hub_integration", 0))
        self.vnet_info = None  # populated separately if needed

    def status_label(self):
        return RequestStatus.label(self.status)

    def status_color(self):
        return RequestStatus.color(self.status)

    def to_dict(self):
        d = {}
        for k, v in self.__dict__.items():
            if k == "vnet_info":
                continue
            if isinstance(v, datetime):
                d[k] = v.strftime("%Y-%m-%d %H:%M:%S")
            else:
                d[k] = v
        d["status_label"] = self.status_label()
        if self.vnet_info:
            d["vnet_info"] = self.vnet_info
        return d


# ── CRUD helpers ─────────────────────────────────────────────────────────────

def create_spoke_request(cidr_needed, purpose, requester_name, ip_range, hub_integration) -> int:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO spoke_requests
               (cidr_needed, purpose, requester_name, ip_range, hub_integration,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(cidr_needed), purpose, requester_name, ip_range,
             1 if hub_integration else 0, RequestStatus.CIDR_REQUESTED, now, now),
        )
        conn.commit()
        req_id = cur.lastrowid
    log.info("[db_utils] INSERT spoke_request #%s → %s", req_id, DB_PATH)
    return req_id


def get_spoke_request(request_id: int):
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM spoke_requests WHERE id = ?", (request_id,)
        ).fetchone()
    if row is None:
        return None
    proxy = RequestProxy(dict(row))
    proxy.vnet_info = get_vnet_info(request_id)
    return proxy


def list_spoke_requests(status_filter: str = None):
    with _conn() as conn:
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM spoke_requests WHERE status = ? ORDER BY created_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM spoke_requests ORDER BY created_at DESC"
            ).fetchall()
    result = []
    for row in rows:
        proxy = RequestProxy(dict(row))
        proxy.vnet_info = get_vnet_info(row["id"])
        result.append(proxy)
    return result


def update_spoke_request(request_id: int, **fields) -> bool:
    if not fields:
        return False
    fields["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [request_id]
    with _conn() as conn:
        conn.execute(f"UPDATE spoke_requests SET {set_clause} WHERE id = ?", values)
        conn.commit()
    log.info("[db_utils] UPDATE spoke_request #%s fields=%s", request_id, list(fields.keys()))
    return True


def get_vnet_info(request_id: int):
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM vnet_info WHERE request_id = ?", (request_id,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    if d.get("outbound_rules"):
        try:
            d["outbound_rules"] = json.loads(d["outbound_rules"])
        except Exception:
            d["outbound_rules"] = []
    return d


# ── Subnet inventory helpers ──────────────────────────────────────────────────
# These operate directly on sqlite3 (no Flask context needed) so agents can
# call them safely from within a Flask request thread.

SUBNET_POOLS = {"10.110": "10.110.0.0/16", "10.119": "10.119.0.0/16"}


def get_pool_key(subnet_str: str):
    """Return the pool key ('10.110' / '10.119') for a subnet, or None."""
    import ipaddress
    try:
        net = ipaddress.ip_network(subnet_str, strict=False)
        for key, cidr in SUBNET_POOLS.items():
            if net.subnet_of(ipaddress.ip_network(cidr)):
                return key
    except Exception:
        pass
    return None


def get_used_subnets_db(pool: str) -> list:
    """Return list of subnet CIDR strings with status 'used' or 'reserved' for a pool."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT subnet FROM subnet_records WHERE pool = ? AND status IN ('used', 'reserved')",
            (pool,),
        ).fetchall()
    return [row["subnet"] for row in rows]


def allocate_subnet_db(subnet: str, pool: str, purpose: str = "",
                       requested_by: str = "", allocated_by: str = "") -> tuple:
    """Insert/update a subnet record as 'used'. Returns (True, msg) or (False, err)."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _conn() as conn:
            existing = conn.execute(
                "SELECT id, status FROM subnet_records WHERE subnet = ?", (subnet,)
            ).fetchone()
            if existing:
                if existing["status"] in ("used", "reserved"):
                    return False, f"Subnet {subnet} is already {existing['status']}"
                conn.execute(
                    "UPDATE subnet_records SET status='used', pool=?, purpose=?, "
                    "requested_by=?, allocated_by=?, allocated_at=?, updated_at=? "
                    "WHERE subnet=?",
                    (pool, purpose, requested_by, allocated_by, now, now, subnet),
                )
            else:
                conn.execute(
                    "INSERT INTO subnet_records "
                    "(subnet, pool, status, purpose, requested_by, allocated_by, "
                    " allocated_at, created_at, updated_at) "
                    "VALUES (?, ?, 'used', ?, ?, ?, ?, ?, ?)",
                    (subnet, pool, purpose, requested_by, allocated_by, now, now, now),
                )
            conn.commit()
        log.info("[db_utils] ALLOCATE subnet %s (pool=%s)", subnet, pool)
        return True, f"Allocated {subnet} successfully"
    except Exception as exc:
        log.error("[db_utils] allocate_subnet_db error: %s", exc)
        return False, str(exc)


def deallocate_subnet_db(subnet: str) -> tuple:
    """Delete a subnet record. Returns (True, msg) or (False, err)."""
    try:
        with _conn() as conn:
            existing = conn.execute(
                "SELECT id FROM subnet_records WHERE subnet = ?", (subnet,)
            ).fetchone()
            if not existing:
                return False, f"Subnet {subnet} not found in database"
            conn.execute("DELETE FROM subnet_records WHERE subnet = ?", (subnet,))
            conn.commit()
        log.info("[db_utils] DEALLOCATE subnet %s", subnet)
        return True, f"Deallocated {subnet} successfully"
    except Exception as exc:
        log.error("[db_utils] deallocate_subnet_db error: %s", exc)
        return False, str(exc)


def get_allocated_subnets_db(pool: str) -> list:
    """Return list of dicts for 'used' subnets in a pool, ordered by subnet."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT subnet, pool, status, purpose, requested_by, allocated_by, allocated_at "
            "FROM subnet_records WHERE pool = ? AND status = 'used' ORDER BY subnet",
            (pool,),
        ).fetchall()
    return [dict(row) for row in rows]


def count_used_subnets_db(pool: str) -> int:
    """Return count of 'used' subnets in a pool."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM subnet_records WHERE pool = ? AND status = 'used'",
            (pool,),
        ).fetchone()
    return row["cnt"] if row else 0


def upsert_vnet_info(request_id: int, **fields):
    """Create or update the vnet_info row for a request."""
    if "outbound_rules" in fields and isinstance(fields["outbound_rules"], list):
        fields["outbound_rules"] = json.dumps(fields["outbound_rules"])

    existing = get_vnet_info(request_id)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")

    with _conn() as conn:
        if existing is None:
            cols = ["request_id", "created_at"] + [k for k in fields if fields[k] is not None]
            placeholders = ", ".join("?" * len(cols))
            values = [request_id, now] + [fields[k] for k in fields if fields[k] is not None]
            conn.execute(
                f"INSERT INTO vnet_info ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
        else:
            non_null = {k: v for k, v in fields.items() if v is not None}
            if non_null:
                set_clause = ", ".join(f"{k} = ?" for k in non_null)
                values = list(non_null.values()) + [request_id]
                conn.execute(
                    f"UPDATE vnet_info SET {set_clause} WHERE request_id = ?", values
                )
        conn.commit()
    log.info("[db_utils] UPSERT vnet_info for request #%s", request_id)
