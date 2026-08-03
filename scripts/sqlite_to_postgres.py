#!/usr/bin/env python3
"""
Copy all AlMadar 360 data from the bundled SQLite file to PostgreSQL.

Run ONCE, offline (app stopped, target Postgres empty):

    DATABASE_URL='postgresql://user:pass@host:5432/networkcopilot' \
    python scripts/sqlite_to_postgres.py [/path/to/requests.db]

It creates the schema in Postgres (via the app's models) and copies every
table, preserving ids and resetting the id sequences. Safe to re-run only
against an empty target.

If you'd rather start clean: skip this, deploy with Postgres, then re-enter
Settings and re-import the subnet inventory from the portal.
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SQLITE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "requests.db")
DBURL = os.environ.get("DATABASE_URL", "")

if not DBURL.startswith(("postgres://", "postgresql://")):
    sys.exit("Set DATABASE_URL to your PostgreSQL connection string first.")
if not os.path.exists(SQLITE):
    sys.exit(f"SQLite file not found: {SQLITE}")

# Importing app builds the Postgres schema (create_all + ensure_table run at
# import time against DATABASE_URL). It does NOT start the web server.
os.environ["SKIP_BOOTSTRAP_MIGRATION"] = "1"   # don't auto-seed from any xlsx
import app          # noqa: E402,F401
import db_backend   # noqa: E402

# Copy order respects FKs (vnet_info → spoke_requests; advisor_conversations →
# advisor_messages/advisor_state). Includes the raw-sqlite tables (agent_chats,
# subscription_inventory, budget_alert_state, advisor_sessions,
# advisor_conversations/messages/state) — they have no FKs to the ORM tables but
# still hold real data (chats, owner/budget/criticality, alert dedup, advisor
# conversation history).
TABLES = ["spoke_requests", "vnet_info", "subnet_records",
          "app_settings", "audit_log", "change_log", "fw_collections",
          "agent_chats", "subscription_inventory", "budget_alert_state",
          "advisor_sessions", "advisor_conversations", "advisor_messages",
          "advisor_state"]

# Columns that are BOOLEAN in Postgres but stored 0/1 in SQLite.
BOOL_COLS = {
    "spoke_requests": {"hub_integration"},
    "vnet_info": {"vpn_zpa_access"},
}


def main():
    src = sqlite3.connect(SQLITE)
    src.row_factory = sqlite3.Row
    total = 0
    for t in TABLES:
        try:
            rows = src.execute(f"SELECT * FROM {t}").fetchall()
        except sqlite3.OperationalError:
            print(f"  {t:16} — not present in source, skipped")
            continue
        if not rows:
            print(f"  {t:16} — 0 rows")
            continue
        cols = list(rows[0].keys())
        bools = BOOL_COLS.get(t, set())
        collist = ", ".join(cols)
        placeholders = ", ".join(["?"] * len(cols))
        with db_backend.connect() as dst:
            existing = dst.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
            if existing:
                print(f"  {t:16} — target already has {existing} rows, SKIPPING "
                      f"(run against an empty database).")
                continue
            for r in rows:
                vals = []
                for c in cols:
                    v = r[c]
                    if c in bools and v is not None:
                        v = bool(v)
                    vals.append(v)
                dst.execute(f"INSERT INTO {t} ({collist}) VALUES ({placeholders})", vals)
            dst.commit()
        # Realign the id sequence so future inserts don't collide.
        if "id" in cols:
            with db_backend.connect() as dst:
                dst.execute(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                    f"(SELECT COALESCE(MAX(id), 1) FROM {t}))")
                dst.commit()
        print(f"  {t:16} — {len(rows)} rows copied")
        total += len(rows)
    src.close()
    print(f"Done. {total} rows migrated to PostgreSQL.")


if __name__ == "__main__":
    main()
