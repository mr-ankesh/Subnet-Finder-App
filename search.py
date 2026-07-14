"""
Global keyword search across requests, VNET info, subnet inventory and the
audit trail. Raw sqlite3 LIKE queries (case-insensitive) — plenty for the
data volumes here; swap for FTS5 if the tables ever grow large.
"""
import logging
import os
import sqlite3

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "data", "requests.db")

_LIMIT = 50  # per category


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, sql, params):
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception as exc:
        log.error("search query failed: %s", exc)
        return []


def global_search(q: str) -> dict:
    """Return {'requests': [...], 'vnets': [...], 'subnets': [...], 'audit': [...]}."""
    q = (q or "").strip()
    if not q:
        return {}
    like = f"%{q.lower()}%"
    # "#12" or a bare number also matches the request id directly
    id_term = q.lstrip("#")
    req_id = int(id_term) if id_term.isdigit() else -1

    with _conn() as conn:
        requests_ = _rows(conn, f"""
            SELECT id, request_type, requester_name, requester_email, purpose, status,
                   allocated_subnet, created_at
            FROM spoke_requests
            WHERE id = ?
               OR LOWER(requester_name)   LIKE ?
               OR LOWER(IFNULL(requester_email, ''))  LIKE ?
               OR LOWER(purpose)          LIKE ?
               OR LOWER(status)           LIKE ?
               OR LOWER(IFNULL(request_type, ''))     LIKE ?
               OR LOWER(IFNULL(allocated_subnet, '')) LIKE ?
               OR LOWER(IFNULL(notes, ''))            LIKE ?
               OR LOWER(IFNULL(details, ''))          LIKE ?
            ORDER BY id DESC LIMIT {_LIMIT}""",
            (req_id, like, like, like, like, like, like, like, like))

        vnets = _rows(conn, f"""
            SELECT request_id, vnet_name, resource_group, subscription_id, region,
                   address_space, subnet_name
            FROM vnet_info
            WHERE LOWER(IFNULL(vnet_name, ''))       LIKE ?
               OR LOWER(IFNULL(resource_group, ''))  LIKE ?
               OR LOWER(IFNULL(subscription_id, '')) LIKE ?
               OR LOWER(IFNULL(address_space, ''))   LIKE ?
               OR LOWER(IFNULL(subnet_name, ''))     LIKE ?
            ORDER BY request_id DESC LIMIT {_LIMIT}""",
            (like, like, like, like, like))

        subnets = _rows(conn, f"""
            SELECT subnet, pool, status, purpose, requested_by, allocated_by, allocated_at
            FROM subnet_records
            WHERE LOWER(subnet)                    LIKE ?
               OR LOWER(IFNULL(purpose, ''))       LIKE ?
               OR LOWER(IFNULL(requested_by, ''))  LIKE ?
               OR LOWER(IFNULL(allocated_by, ''))  LIKE ?
            ORDER BY id DESC LIMIT {_LIMIT}""",
            (like, like, like, like))

        audit_ = _rows(conn, f"""
            SELECT ts, actor, actor_role, action, request_id, summary
            FROM audit_log
            WHERE LOWER(actor)              LIKE ?
               OR LOWER(action)             LIKE ?
               OR LOWER(IFNULL(summary,'')) LIKE ?
               OR request_id = ?
            ORDER BY id DESC LIMIT {_LIMIT}""",
            (like, like, like, req_id))

    return {"requests": requests_, "vnets": vnets, "subnets": subnets, "audit": audit_}
