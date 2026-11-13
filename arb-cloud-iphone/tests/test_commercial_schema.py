"""Contract tests for the inactive commercial schema."""
import sqlite3
import unittest

from app.commercial_schema import migrate_commercial_schema


class CommercialSchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        migrate_commercial_schema(self.db)

    def tearDown(self):
        self.db.close()

    def test_idempotent(self):
        migrate_commercial_schema(self.db)

    def test_unique_email(self):
        self.db.execute("INSERT INTO commercial_users(id,email_normalized,password_hash) VALUES ('u1','a@example.com','argon2-hash')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO commercial_users(id,email_normalized,password_hash) VALUES ('u2','a@example.com','argon2-hash')")

    def test_membership_requires_existing_tenant(self):
        self.db.execute("INSERT INTO commercial_users(id,email_normalized,password_hash) VALUES ('u1','a@example.com','hash')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO commercial_memberships(tenant_id,user_id,role) VALUES ('missing','u1','viewer')")

    def test_invalid_role_rejected(self):
        self.db.execute("INSERT INTO commercial_tenants(id,display_name,kind) VALUES ('t1','Customer','customer')")
        self.db.execute("INSERT INTO commercial_users(id,email_normalized,password_hash) VALUES ('u1','a@example.com','hash')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO commercial_memberships(tenant_id,user_id,role) VALUES ('t1','u1','superuser')")

    def test_profit_fee_disabled_by_default(self):
        self.db.execute("INSERT INTO commercial_tenants(id,display_name,kind) VALUES ('t1','Customer','customer')")
        self.db.execute("INSERT INTO commercial_profit_fee_ledger(id,tenant_id,position_external_id,realised_profit_cents,fee_basis_cents,fee_amount_cents) VALUES ('f1','t1','p1',10000,10000,1000)")
        status = self.db.execute("SELECT status FROM commercial_profit_fee_ledger WHERE id='f1'").fetchone()[0]
        self.assertEqual(status, "disabled")


if __name__ == "__main__":
    unittest.main()
