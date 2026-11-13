"""SQLite tenant-integrity regression tests (no network, no live customer data)."""
import sqlite3
import unittest

from app.customer_schema import migrate_customer_platform


class CustomerSchemaIsolationTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("PRAGMA foreign_keys=ON")
        migrate_customer_platform(self.con)
        self.con.executemany(
            "INSERT INTO customer_tenants(id, display_name) VALUES (?,?)",
            [("a", "Tenant A"), ("b", "Tenant B")],
        )
        self.con.executemany(
            """INSERT INTO customer_bookmaker_accounts
               (id, tenant_id, bookmaker, label)
               VALUES (?,?,?,?)""",
            [("a-account", "a", "Sportsbet", "Main"),
             ("b-account", "b", "Sportsbet", "Main")],
        )
        self.con.executemany(
            """INSERT INTO customer_positions
               (id, tenant_id, event_id, market_id)
               VALUES (?,?,?,?)""",
            [("a-position", "a", "e1", "m1"),
             ("b-position", "b", "e2", "m2")],
        )
        self.con.executemany(
            """INSERT INTO customer_users
               (id, tenant_id, email, password_hash)
               VALUES (?,?,?,?)""",
            [("a-user", "a", "a@example.invalid", "argon2-placeholder"),
             ("b-user", "b", "b@example.invalid", "argon2-placeholder")],
        )

    def tearDown(self):
        self.con.close()

    def test_idempotent_migration(self):
        migrate_customer_platform(self.con)
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM customer_tenants").fetchone()[0], 2
        )

    def test_cross_tenant_leg_account_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                """INSERT INTO customer_position_legs
                   (id, tenant_id, position_id, account_id, outcome_id, odds, stake_cents)
                   VALUES (?,?,?,?,?,?,?)""",
                ("bad", "a", "a-position", "b-account", "home", 2.1, 5000),
            )

    def test_cross_tenant_leg_position_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                """INSERT INTO customer_position_legs
                   (id, tenant_id, position_id, account_id, outcome_id, odds, stake_cents)
                   VALUES (?,?,?,?,?,?,?)""",
                ("bad", "a", "b-position", "a-account", "home", 2.1, 5000),
            )

    def test_valid_same_tenant_leg_allowed(self):
        self.con.execute(
            """INSERT INTO customer_position_legs
               (id, tenant_id, position_id, account_id, outcome_id, odds, stake_cents)
               VALUES (?,?,?,?,?,?,?)""",
            ("good", "a", "a-position", "a-account", "home", 2.1, 5000),
        )
        self.assertEqual(
            self.con.execute("SELECT tenant_id FROM customer_position_legs").fetchone()[0],
            "a",
        )

    def test_cross_tenant_session_user_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                """INSERT INTO customer_sessions(token_hash, tenant_id, user_id, expires_at)
                   VALUES (?,?,?,?)""",
                ("hash", "a", "b-user", "2030-01-01"),
            )

    def test_cross_tenant_bankroll_account_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                """INSERT INTO customer_bankroll_entries
                   (id, tenant_id, account_id, amount_cents, entry_type)
                   VALUES (?,?,?,?,?)""",
                ("bad", "a", "b-account", 100, "deposit"),
            )

    def test_cross_tenant_audit_actor_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                """INSERT INTO customer_audit_log
                   (id, tenant_id, actor_user_id, action)
                   VALUES (?,?,?,?)""",
                ("bad", "a", "b-user", "login"),
            )

    def test_negative_opening_balance_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                """INSERT INTO customer_bookmaker_accounts
                   (id, tenant_id, bookmaker, label, opening_balance_cents)
                   VALUES (?,?,?,?,?)""",
                ("bad", "a", "Betfair", "Negative", -1),
            )


if __name__ == "__main__":
    unittest.main()
