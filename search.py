"""
Global keyword search across requests, VNET info, subnet inventory and the
audit trail. Raw sqlite3 LIKE queries (case-insensitive) — plenty for the
data volumes here; swap for FTS5 if the tables ever grow large.
"""
import logging

import db_backend

log = logging.getLogger(__name__)

_LIMIT = 50  # per category


def _conn():
    return db_backend.connect()


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
               OR LOWER(COALESCE(requester_email, ''))  LIKE ?
               OR LOWER(purpose)          LIKE ?
               OR LOWER(status)           LIKE ?
               OR LOWER(COALESCE(request_type, ''))     LIKE ?
               OR LOWER(COALESCE(allocated_subnet, '')) LIKE ?
               OR LOWER(COALESCE(notes, ''))            LIKE ?
               OR LOWER(COALESCE(details, ''))          LIKE ?
            ORDER BY id DESC LIMIT {_LIMIT}""",
            (req_id, like, like, like, like, like, like, like, like))

        vnets = _rows(conn, f"""
            SELECT request_id, vnet_name, resource_group, subscription_id, region,
                   address_space, subnet_name
            FROM vnet_info
            WHERE LOWER(COALESCE(vnet_name, ''))       LIKE ?
               OR LOWER(COALESCE(resource_group, ''))  LIKE ?
               OR LOWER(COALESCE(subscription_id, '')) LIKE ?
               OR LOWER(COALESCE(address_space, ''))   LIKE ?
               OR LOWER(COALESCE(subnet_name, ''))     LIKE ?
            ORDER BY request_id DESC LIMIT {_LIMIT}""",
            (like, like, like, like, like))

        subnets = _rows(conn, f"""
            SELECT subnet, pool, status, purpose, requested_by, allocated_by, allocated_at
            FROM subnet_records
            WHERE LOWER(subnet)                    LIKE ?
               OR LOWER(COALESCE(purpose, ''))       LIKE ?
               OR LOWER(COALESCE(requested_by, ''))  LIKE ?
               OR LOWER(COALESCE(allocated_by, ''))  LIKE ?
            ORDER BY id DESC LIMIT {_LIMIT}""",
            (like, like, like, like))

        audit_ = _rows(conn, f"""
            SELECT ts, actor, actor_role, action, request_id, summary
            FROM audit_log
            WHERE LOWER(actor)              LIKE ?
               OR LOWER(action)             LIKE ?
               OR LOWER(COALESCE(summary,'')) LIKE ?
               OR request_id = ?
            ORDER BY id DESC LIMIT {_LIMIT}""",
            (like, like, like, req_id))

    return {"requests": requests_, "vnets": vnets, "subnets": subnets, "audit": audit_}
