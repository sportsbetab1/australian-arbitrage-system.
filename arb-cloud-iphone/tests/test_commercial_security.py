import sqlite3
import unittest
from app.commercial_schema import migrate_commercial_schema, migrate_legacy_tenant_columns
from app.commercial_security import (
    AccessDenied, create_user, authenticate, create_session, resolve_session,
    revoke_session, require_member, tenant_rows, request_sensitive_action,
    approve_sensitive_action, consume_approved_action,
)

class TenantSecurityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        for table in ("accounts", "positions", "bankroll_ledger"):
            self.db.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, label TEXT)")
        migrate_commercial_schema(self.db)
        migrate_legacy_tenant_columns(self.db)
        for tenant, kind in (("internal","internal"),("a","customer"),("b","customer")):
            self.db.execute("INSERT INTO commercial_tenants(id,display_name,kind,status) VALUES (?,?,?,'active')", (tenant,tenant,kind))
        self.a = create_user(self.db,"a@example.com","long-password-for-a")
        self.b = create_user(self.db,"b@example.com","long-password-for-b")
        self.h = create_user(self.db,"h@example.com","long-password-for-h")
        self.j = create_user(self.db,"j@example.com","long-password-for-j")
        for uid, tenant, role in ((self.a,"a","tenant_admin"),(self.b,"b","tenant_admin"),
                                  (self.h,"internal","platform_owner"),(self.j,"internal","platform_owner")):
            self.db.execute("INSERT INTO commercial_memberships(tenant_id,user_id,role) VALUES (?,?,?)",(tenant,uid,role))
        for table in ("accounts","positions","bankroll_ledger"):
            self.db.execute(f"INSERT INTO {table}(label,tenant_id) VALUES ('secret-a','a')")
            self.db.execute(f"INSERT INTO {table}(label,tenant_id) VALUES ('secret-b','b')")

    def tearDown(self):
        self.db.close()

    def test_isolation_all_three_tables(self):
        for table in ("accounts","positions","bankroll_ledger"):
            self.assertEqual([r["label"] for r in tenant_rows(self.db,self.a,"a",table)], ["secret-a"])
            self.assertEqual([r["label"] for r in tenant_rows(self.db,self.b,"b",table)], ["secret-b"])
            with self.assertRaises(AccessDenied):
                tenant_rows(self.db,self.a,"b",table)
            with self.assertRaises(AccessDenied):
                tenant_rows(self.db,self.b,"a",table)

    def test_login_session_and_revocation(self):
        self.db.execute("UPDATE commercial_users SET email_verified_at=CURRENT_TIMESTAMP WHERE id=?", (self.a,))
        uid = authenticate(self.db,"A@EXAMPLE.COM","long-password-for-a")
        token = create_session(self.db,uid)
        self.assertEqual(resolve_session(self.db,token),self.a)
        revoke_session(self.db,token)
        with self.assertRaises(AccessDenied):
            resolve_session(self.db,token)
        with self.assertRaises(AccessDenied):
            authenticate(self.db,"a@example.com","wrong-password")

    def test_unverified_login_denied(self):
        with self.assertRaises(AccessDenied):
            authenticate(self.db,"b@example.com","long-password-for-b")

    def test_dual_owner_approval(self):
        approval = request_sensitive_action(self.db,self.h,"change_profit_fee","sha256:example")
        with self.assertRaises(AccessDenied):
            approve_sensitive_action(self.db,self.h,approval)
        with self.assertRaises(AccessDenied):
            approve_sensitive_action(self.db,self.a,approval)
        approve_sensitive_action(self.db,self.j,approval)
        self.assertEqual(self.db.execute("SELECT status FROM commercial_owner_approvals WHERE id=?",(approval,)).fetchone()[0],"approved")
        with self.assertRaises(AccessDenied):
            approve_sensitive_action(self.db,self.j,approval)
        consume_approved_action(self.db,approval,"change_profit_fee","sha256:example")
        with self.assertRaises(AccessDenied):
            consume_approved_action(self.db,approval,"change_profit_fee","sha256:example")

if __name__ == "__main__":
    unittest.main()
