"""One-time internal tenant backfill; requires operator-confirmed backup.

Never run against customer records. This only assigns NULL legacy records to
an explicitly selected internal tenant, and aborts if foreign tenant IDs exist.
"""
import argparse
import sqlite3
from app.database import get_db
from app.commercial_schema import migrate_commercial_schema, migrate_legacy_tenant_columns

def backfill_internal(con, tenant_id):
    con.execute("PRAGMA foreign_keys=ON")
    row=con.execute("SELECT kind FROM commercial_tenants WHERE id=?",(tenant_id,)).fetchone()
    if not row or row[0]!="internal":
        raise ValueError("Existing internal tenant required")
    for table in ("accounts","positions","bankroll_ledger"):
        # This operation must not overwrite any assigned customer records.
        con.execute(f"UPDATE {table} SET tenant_id=? WHERE tenant_id IS NULL",(tenant_id,))
    # Detect cross-tenant position leg links before allowing customer data use.
    mismatch=con.execute("""SELECT COUNT(*) FROM position_legs l
      JOIN positions p ON p.id=l.position_id
      JOIN accounts a ON a.id=l.account_id
      WHERE p.tenant_id IS NOT a.tenant_id""").fetchone()[0]
    if mismatch:
        raise ValueError(f"Cross-tenant position legs detected: {mismatch}")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--internal-tenant-id",required=True)
    parser.add_argument("--backup-confirmed",action="store_true")
    args=parser.parse_args()
    if not args.backup_confirmed:
        parser.error("Backup confirmation required")
    with get_db() as con:
        migrate_commercial_schema(con)
        migrate_legacy_tenant_columns(con)
        con.execute("BEGIN IMMEDIATE")
        backfill_internal(con,args.internal_tenant_id)
    print("Legacy records mapped to internal tenant after integrity check.")

if __name__=="__main__":
    main()
