"""
One-time migration script: imports subnet inventory from subnets.xlsx into the
subnet_records table in requests.db.

Usage (run once, inside the container or on the host with the same Python env):

    python migrate_excel_to_db.py

Safe to re-run — it skips rows whose subnet is already present in the DB.
Only 'used' and 'reserved' rows from Excel are imported; 'unused' entries are
discarded (free space is computed dynamically from the DB).

After a successful run, rename or move subnets.xlsx so the app no longer sees it:
    mv data/subnets.xlsx data/subnets.xlsx.bak
"""
import ipaddress
import os
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
EXCEL_PATH = os.path.join(HERE, "data", "subnets.xlsx")
DB_PATH    = os.path.join(HERE, "data", "requests.db")

POOLS = {"10.110": "10.110.0.0/16", "10.119": "10.119.0.0/16"}


def get_pool_key(subnet_str: str):
    try:
        net = ipaddress.ip_network(subnet_str, strict=False)
        for key, cidr in POOLS.items():
            if net.subnet_of(ipaddress.ip_network(cidr)):
                return key
    except Exception:
        pass
    return None


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subnet_records (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            subnet       TEXT    NOT NULL UNIQUE,
            pool         TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'used',
            purpose      TEXT,
            requested_by TEXT,
            allocated_by TEXT,
            allocated_at TEXT,
            created_at   TEXT,
            updated_at   TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_subnet_records_subnet ON subnet_records(subnet)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_subnet_records_pool   ON subnet_records(pool)")
    conn.commit()


def run():
    if not os.path.exists(EXCEL_PATH):
        print(f"[ERROR] Excel file not found: {EXCEL_PATH}")
        sys.exit(1)
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database not found: {DB_PATH}")
        print("        Start the app once first so it creates the schema, then re-run this script.")
        sys.exit(1)

    try:
        import pandas as pd
    except ImportError:
        print("[ERROR] pandas is required. Run: pip install pandas openpyxl")
        sys.exit(1)

    print(f"[info] Reading {EXCEL_PATH} ...")
    df = pd.read_excel(EXCEL_PATH, dtype=str).fillna("")
    df.columns = [c.strip().replace(" ", "") for c in df.columns]

    if "Subnet" not in df.columns or "Status" not in df.columns:
        print("[ERROR] Excel file must have 'Subnet' and 'Status' columns.")
        sys.exit(1)

    df["Status"] = df["Status"].str.strip().str.lower()
    rows_to_import = df[df["Status"].isin(["used", "reserved"])]
    print(f"[info] Found {len(rows_to_import)} used/reserved rows (skipping {len(df) - len(rows_to_import)} unused rows).")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_table(conn)

    imported = skipped_dup = skipped_no_pool = errors = 0
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    for _, row in rows_to_import.iterrows():
        subnet_str = str(row.get("Subnet", "")).strip()
        status     = str(row.get("Status", "used")).strip()
        if not subnet_str:
            continue

        pool_key = get_pool_key(subnet_str)
        if not pool_key:
            print(f"  [SKIP] {subnet_str} — not in any known pool")
            skipped_no_pool += 1
            continue

        # Normalise CIDR representation
        try:
            subnet_str = str(ipaddress.ip_network(subnet_str, strict=False))
        except Exception:
            print(f"  [SKIP] {subnet_str} — invalid CIDR")
            errors += 1
            continue

        # Check for duplicates
        existing = conn.execute("SELECT id FROM subnet_records WHERE subnet = ?", (subnet_str,)).fetchone()
        if existing:
            print(f"  [DUP]  {subnet_str} — already in DB, skipping")
            skipped_dup += 1
            continue

        allocated_at_raw = str(row.get("AllocationTime", "")).strip()
        try:
            allocated_at = datetime.strptime(allocated_at_raw[:19], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            allocated_at = now

        try:
            conn.execute(
                "INSERT INTO subnet_records "
                "(subnet, pool, status, purpose, requested_by, allocated_by, allocated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    subnet_str,
                    pool_key,
                    status,
                    str(row.get("Purpose",     "")).strip() or None,
                    str(row.get("RequestedBy", "")).strip() or None,
                    str(row.get("AllocatedBy", "")).strip() or None,
                    allocated_at,
                    now,
                    now,
                ),
            )
            print(f"  [OK]   {subnet_str} ({status}, pool={pool_key})")
            imported += 1
        except Exception as exc:
            print(f"  [ERR]  {subnet_str} — {exc}")
            errors += 1

    conn.commit()
    conn.close()

    print()
    print(f"[done] Imported: {imported} | Duplicates skipped: {skipped_dup} | "
          f"No-pool skipped: {skipped_no_pool} | Errors: {errors}")
    if imported > 0:
        print()
        print("Next step: rename the Excel file so it's no longer used by the app:")
        print(f"    mv {EXCEL_PATH} {EXCEL_PATH}.bak")


if __name__ == "__main__":
    run()
