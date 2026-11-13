"""Explicit, offline tenant provisioning. Never expose as public HTTP route.

Usage:
  DB_PATH=/path/to/backup-tested.db python -m scripts.provision_commercial \
      --tenant-id <uuid> --name "Customer" --email customer@example.com

A verified email is required before login; verification is handled separately.
Passwords are prompted interactively and never passed on command line.
"""
import argparse
import getpass
import uuid
from app.database import get_db
from app.commercial_schema import migrate_commercial_schema, migrate_legacy_tenant_columns
from app.commercial_security import create_user

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id",required=True)
    parser.add_argument("--name",required=True)
    parser.add_argument("--email",required=True)
    args = parser.parse_args()
    uuid.UUID(args.tenant_id)
    password = getpass.getpass("Password (minimum 12 characters): ")
    with get_db() as con:
        migrate_commercial_schema(con)
        migrate_legacy_tenant_columns(con)
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO commercial_tenants(id,display_name,kind,status) VALUES (?,?,'customer','active')",
                    (args.tenant_id,args.name))
        uid = create_user(con,args.email,password)
        con.execute("INSERT INTO commercial_memberships(tenant_id,user_id,role) VALUES (?,?,'tenant_admin')",
                    (args.tenant_id,uid))
    print("Tenant and unverified customer created:",args.tenant_id)
    print("Account cannot log in until verified via a secure verification workflow.")

if __name__ == "__main__":
    main()
