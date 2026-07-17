"""
Change ledger — the platform's undo history.

Every mutating operation records WHAT it changed, the state BEFORE the
change, and a machine-executable revert plan (revert_op + revert_params).
The /admin/changes page lists entries and can restore any of them to the
earlier state — including decommissions and in-place modifications.

Raw sqlite3 (same pattern as audit.py) so batch flows and agents can write
without Flask-SQLAlchemy session scoping. Recording is best-effort: a ledger
failure must never break the operation being recorded.
"""
import json
import logging
from datetime import datetime

import db_backend

log = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_REVERTED = "reverted"


def _conn():
    return db_backend.connect()


def ensure_table():
    with _conn() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS change_log (
                id             {db_backend.AUTOINC_PK},
                ts             TEXT NOT NULL,
                actor          TEXT NOT NULL,
                request_id     INTEGER,
                action         TEXT NOT NULL,
                target         TEXT,
                summary        TEXT,
                before         TEXT,
                after          TEXT,
                revert_op      TEXT,
                revert_params  TEXT,
                status         TEXT NOT NULL DEFAULT 'active',
                reverted_ts    TEXT,
                reverted_by    TEXT,
                revert_message TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_change_request ON change_log(request_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_change_status ON change_log(status)")


def record(action: str, actor: str = "system", request_id=None, target: str = "",
           summary: str = "", before=None, after=None,
           revert_op: str = None, revert_params: dict = None):
    """Append a change entry. Never raises."""
    try:
        ensure_table()
        with _conn() as conn:
            cid = db_backend.insert_returning_id(
                conn,
                """INSERT INTO change_log
                   (ts, actor, request_id, action, target, summary, before, after,
                    revert_op, revert_params)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                 (actor or "system")[:200],
                 int(request_id) if request_id is not None else None,
                 action, (target or "")[:300], (summary or "")[:500],
                 json.dumps(before) if before is not None else None,
                 json.dumps(after) if after is not None else None,
                 revert_op, json.dumps(revert_params) if revert_params else None))
            conn.commit()
            return cid
    except Exception as exc:
        log.error("changes.record failed (%s): %s", action, exc)
        return None


def _row_to_dict(r) -> dict:
    d = dict(r)
    for k in ("before", "after", "revert_params"):
        try:
            d[k] = json.loads(d[k]) if d[k] else None
        except Exception:
            d[k] = None
    return d


def list_changes(request_id=None, status: str = None, limit: int = 200) -> list:
    try:
        ensure_table()
        where, params = [], []
        if request_id is not None:
            where.append("request_id = ?"); params.append(int(request_id))
        if status:
            where.append("status = ?"); params.append(status)
        sql = "SELECT * FROM change_log"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with _conn() as conn:
            return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception as exc:
        log.error("changes.list_changes failed: %s", exc)
        return []


def get_change(cid: int):
    try:
        ensure_table()
        with _conn() as conn:
            r = conn.execute("SELECT * FROM change_log WHERE id = ?", (int(cid),)).fetchone()
        return _row_to_dict(r) if r else None
    except Exception:
        return None


def _mark(cid: int, status: str, actor: str, message: str):
    with _conn() as conn:
        conn.execute(
            "UPDATE change_log SET status=?, reverted_ts=?, reverted_by=?, revert_message=? WHERE id=?",
            (status, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
             actor[:200], (message or "")[:500], int(cid)))


# ── Revert engine ────────────────────────────────────────────────────────────
# Each op restores the recorded 'before' state (or removes what was created).

def _revert_dispatch(op: str, p: dict) -> dict:
    import azure_tools
    from config import cfg

    if op == "delete_route":
        return azure_tools.delete_route_from_table(
            p["table"], p["rg"], p["route_name"], subscription_id=p.get("sub"))
    if op == "restore_route":
        r = p["route"]
        return azure_tools.add_route_to_table(
            p["table"], p["rg"], r["name"], r["prefix"], r.get("next_hop_type", "VirtualAppliance"),
            next_hop_ip=r.get("next_hop_ip") or None, subscription_id=p.get("sub"),
            on_conflict="replace")
    if op == "restore_routes":
        results = [azure_tools.add_route_to_table(
            p["table"], p["rg"], r["name"], r["prefix"], r.get("next_hop_type", "VirtualAppliance"),
            next_hop_ip=r.get("next_hop_ip") or None, subscription_id=p.get("sub"),
            on_conflict="replace") for r in p.get("routes", [])]
        ok = all(x.get("success") for x in results)
        return {"success": ok, "message": "; ".join(str(x.get("message", "")) for x in results)}
    if op == "remove_fw_rule":
        return azure_tools.remove_firewall_rule(p["rule_name"], rcg_name=p.get("rcg"),
                                                collection_name=p.get("collection"))
    if op == "restore_fw_rule":
        return azure_tools.restore_firewall_rule(p["rule"], rcg_name=p.get("rcg"),
                                                 collection_name=p.get("collection"))
    if op == "add_fw_cidr":
        return azure_tools.add_cidr_to_firewall_rule(p["rule_name"], p["cidr"])
    if op == "remove_fw_cidr":
        return azure_tools.remove_cidr_from_firewall_rule(p["rule_name"], p["cidr"])
    if op == "add_nsg_cidr":
        return azure_tools.add_cidr_to_nsg_rule(p["nsg"], p["rg"], p["rule"], p["cidr"],
                                                subscription_id=p.get("sub"))
    if op == "remove_nsg_cidr":
        return azure_tools.remove_cidr_from_nsg_rule(p["nsg"], p["rg"], p["rule"], p["cidr"],
                                                     subscription_id=p.get("sub"))
    if op == "delete_peerings":
        return azure_tools.delete_hub_spoke_peerings(
            p["sub"], p["rg"], p["vnet"],
            spoke_to_hub_name=p.get("s2h"), hub_to_spoke_name=p.get("h2s"))
    if op == "restore_peerings":
        return azure_tools.peer_hub_vnet(
            spoke_subscription_id=p["sub"], spoke_resource_group=p["rg"],
            spoke_vnet_name=p["vnet"], spoke_address_space=p.get("addr", ""),
            spoke_to_hub_name=p.get("s2h"), hub_to_spoke_name=p.get("h2s"),
            on_conflict="replace")
    if op == "delete_vnet":
        return azure_tools.delete_spoke_vnet(p["sub"], p["rg"], p["vnet"])
    if op == "restore_vnet":
        return azure_tools.restore_vnet(p["sub"], p["rg"], p["vnet"], p["location"],
                                        p.get("address_space", []), p.get("subnets", []))
    if op == "delete_spoke_rt":
        return azure_tools.delete_spoke_route_table(p["sub"], p["rg"], p["vnet"], p["rt"])
    if op == "restore_spoke_rt":
        return azure_tools.restore_spoke_route_table(
            p["sub"], p["rg"], p["vnet"], p["rt"], p.get("location", ""),
            p.get("routes", []), p.get("assigned_subnets", []))
    if op == "assign_subnet_rt":
        return azure_tools.assign_route_table_to_subnet(
            p["sub"], p["rg"], p["vnet"], p["subnet"], p.get("rt_id"))
    if op == "delete_dns_record":
        return azure_tools.delete_dns_record(p["zone"], p["rtype"], p["name"])
    if op == "restore_dns_record":
        return azure_tools.restore_dns_record(p["zone"], p["rtype"], p["name"],
                                              p.get("values", []), p.get("ttl", 3600))
    if op == "delete_dns_zone":
        return azure_tools.delete_dns_zone(p["zone"])
    if op == "delete_dns_zone_link":
        return azure_tools.delete_dns_zone_link(p["zone"], p["link"])
    if op == "release_cidr":
        from db_utils import deallocate_subnet_db
        ok, msg = deallocate_subnet_db(p["subnet"])
        return {"success": ok or "not found" in str(msg).lower(), "message": msg}
    if op == "allocate_cidr":
        from db_utils import allocate_subnet_db
        ok, msg = allocate_subnet_db(p["subnet"], p["pool"], p.get("purpose", ""),
                                     p.get("requested_by", ""), p.get("allocated_by", ""))
        return {"success": ok, "message": msg}
    return {"success": False, "message": f"Unknown revert operation '{op}'."}


def execute_revert(cid: int, actor: str) -> dict:
    """Restore the earlier state recorded in change #cid. Fully audited."""
    import audit
    entry = get_change(cid)
    if not entry:
        return {"success": False, "message": f"Change #{cid} not found."}
    if entry["status"] == STATUS_REVERTED:
        return {"success": False, "message": f"Change #{cid} was already reverted."}
    if not entry.get("revert_op"):
        return {"success": False, "message": "This change has no stored revert plan."}
    try:
        res = _revert_dispatch(entry["revert_op"], entry.get("revert_params") or {})
    except Exception as exc:
        log.error("changes.execute_revert #%s failed: %s", cid, exc)
        res = {"success": False, "message": str(exc)}
    if res.get("success"):
        _mark(cid, STATUS_REVERTED, actor, str(res.get("message", ""))[:500])
        # The revert itself is a change — record it so it can be re-applied.
        record(action=f"revert:{entry['action']}", actor=actor,
               request_id=entry.get("request_id"),
               target=entry.get("target", ""),
               summary=f"Reverted change #{cid}: {entry.get('summary', '')}"[:490],
               before=entry.get("after"), after=entry.get("before"))
    audit.record("change_reverted" if res.get("success") else "change_revert_failed",
                 actor=actor, actor_role="admin", request_id=entry.get("request_id"),
                 summary=f"Revert change #{cid} ({entry['action']} — {entry.get('target', '')}): "
                         f"{str(res.get('message', ''))[:200]}",
                 data={"change_id": cid, "revert_op": entry.get("revert_op"),
                       "success": bool(res.get("success"))})
    return res
