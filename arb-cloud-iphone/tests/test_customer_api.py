"""End-to-end customer session and tenant isolation tests on a temporary SQLite DB."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.customer_schema import migrate_customer_platform
from app.main import app

_hasher = PasswordHasher()


class CustomerAPIIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "customers.sqlite3")

        def connect():
            con = sqlite3.connect(self.db_path)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            return con

        self.connect = connect
        with connect() as con:
            migrate_customer_platform(con)
            con.executemany(
                "INSERT INTO customer_tenants(id,display_name,status) VALUES (?,?,?)",
                [("tenant-a", "A", "active"), ("tenant-b", "B", "active")],
            )
            con.executemany(
                """INSERT INTO customer_users(id,tenant_id,email,password_hash)
                   VALUES (?,?,?,?)""",
                [("user-a", "tenant-a", "a@example.invalid", _hasher.hash("secure-pass-a")),
                 ("user-b", "tenant-b", "b@example.invalid", _hasher.hash("secure-pass-b"))],
            )
            con.executemany(
                """INSERT INTO customer_bookmaker_accounts
                   (id,tenant_id,bookmaker,label) VALUES (?,?,?,?)""",
                [("account-a", "tenant-a", "Betfair", "A"),
                 ("account-b", "tenant-b", "Sportsbet", "B")],
            )
            con.executemany(
                """INSERT INTO customer_positions
                   (id,tenant_id,event_id,market_id) VALUES (?,?,?,?)""",
                [("position-a", "tenant-a", "event-a", "market-a"),
                 ("position-b", "tenant-b", "event-b", "market-b")],
            )
            con.executemany(
                """INSERT INTO customer_bankroll_entries
                   (id,tenant_id,amount_cents,entry_type) VALUES (?,?,?,?)""",
                [("ledger-a", "tenant-a", 100, "deposit"),
                 ("ledger-b", "tenant-b", 200, "deposit")],
            )
        with connect() as con:
            con.execute("UPDATE customer_users SET role='owner' WHERE id='user-a'")
        self.env_patch = patch.dict(os.environ, {"AUTH_COOKIE_SECURE": "false"})
        self.env_patch.start()
        self.patch = patch("app.customer_api.get_db", self.connect)
        self.patch.start()
        self.client = TestClient(app)
        self.origin = {"Origin": "http://testserver"}

    def tearDown(self):
        self.client.close()
        self.patch.stop()
        self.env_patch.stop()
        self.temp.cleanup()

    def login(self, tenant, email, password):
        return self.client.post(
            "/api/customer/login",
            json={"tenant_id": tenant, "email": email, "password": password},
            headers=self.origin,
        )

    def test_anonymous_access_denied(self):
        for path in ("/me", "/accounts", "/positions", "/bankroll", "/positions/position-a"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get("/api/customer" + path).status_code, 401)

    def test_invalid_credentials_rejected(self):
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "incorrect").status_code, 401)
        self.assertEqual(self.login("tenant-b", "a@example.invalid", "secure-pass-a").status_code, 401)

    def test_five_failed_logins_lock_account(self):
        for attempt in range(5):
            self.assertEqual(
                self.login("tenant-a", "a@example.invalid", "wrong").status_code, 401
            )
        self.assertEqual(
            self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 423
        )
        with self.connect() as con:
            row = con.execute(
                "SELECT failed_attempts, locked_until FROM customer_users WHERE id='user-a'"
            ).fetchone()
            self.assertEqual(row["failed_attempts"], 5)
            self.assertIsNotNone(row["locked_until"])
        # A different tenant's account must not be locked as collateral damage.
        self.assertEqual(
            self.login("tenant-b", "b@example.invalid", "secure-pass-b").status_code, 200
        )

    def test_expired_lock_resets_after_success(self):
        with self.connect() as con:
            con.execute(
                "UPDATE customer_users SET failed_attempts=5, locked_until=? WHERE id='user-a'",
                ("2020-01-01T00:00:00+00:00",),
            )
        self.assertEqual(
            self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200
        )
        with self.connect() as con:
            row = con.execute(
                "SELECT failed_attempts, locked_until FROM customer_users WHERE id='user-a'"
            ).fetchone()
            self.assertEqual(row["failed_attempts"], 0)
            self.assertIsNone(row["locked_until"])

    def test_customer_a_cannot_read_customer_b_data(self):
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200)
        self.assertEqual([r["id"] for r in self.client.get("/api/customer/accounts").json()], ["account-a"])
        self.assertEqual([r["id"] for r in self.client.get("/api/customer/positions").json()], ["position-a"])
        self.assertEqual([r["id"] for r in self.client.get("/api/customer/bankroll").json()], ["ledger-a"])
        self.assertEqual(self.client.get("/api/customer/positions/position-b").status_code, 404)
        self.assertEqual(self.client.get("/api/customer/positions/position-a").status_code, 200)

    def test_customer_b_cannot_read_customer_a_data(self):
        self.assertEqual(self.login("tenant-b", "b@example.invalid", "secure-pass-b").status_code, 200)
        self.assertEqual([r["id"] for r in self.client.get("/api/customer/accounts").json()], ["account-b"])
        self.assertEqual(self.client.get("/api/customer/positions/position-a").status_code, 404)

    def test_owner_creates_only_own_tenant_account(self):
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200)
        created = self.client.post(
            "/api/customer/accounts",
            json={"bookmaker": "Unibet", "label": "Secondary", "opening_balance_cents": 2500},
            headers=self.origin,
        )
        self.assertEqual(created.status_code, 201, created.text)
        account_id = created.json()["id"]
        with self.connect() as con:
            row = con.execute(
                "SELECT tenant_id FROM customer_bookmaker_accounts WHERE id=?", (account_id,)
            ).fetchone()
            self.assertEqual(row["tenant_id"], "tenant-a")
            audit = con.execute(
                "SELECT tenant_id, action FROM customer_audit_log WHERE action='customer_account_created'"
            ).fetchone()
            self.assertEqual(audit["tenant_id"], "tenant-a")
        self.assertEqual(
            self.client.post(
                "/api/customer/accounts",
                json={"bookmaker": "Unibet", "label": "Secondary"},
                headers=self.origin,
            ).status_code, 409
        )

    def test_member_cannot_create_account(self):
        self.assertEqual(self.login("tenant-b", "b@example.invalid", "secure-pass-b").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/customer/accounts",
                json={"bookmaker": "Betfair", "label": "New"},
                headers=self.origin,
            ).status_code, 403
        )

    def test_owner_bankroll_entry_is_tenant_scoped(self):
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200)
        response = self.client.post(
            "/api/customer/bankroll",
            json={"account_id": "account-a", "entry_type": "deposit", "amount_cents": 7500},
            headers=self.origin,
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(
            self.client.post(
                "/api/customer/bankroll",
                json={"account_id": "account-b", "entry_type": "deposit", "amount_cents": 100},
                headers=self.origin,
            ).status_code, 404
        )
        with self.connect() as con:
            row = con.execute(
                "SELECT tenant_id, amount_cents FROM customer_bankroll_entries WHERE id=?",
                (response.json()["id"],),
            ).fetchone()
            self.assertEqual((row["tenant_id"], row["amount_cents"]), ("tenant-a", 7500))
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM customer_audit_log WHERE action='customer_bankroll_entry_created'"
                ).fetchone()[0], 1
            )

    def test_member_cannot_write_bankroll(self):
        self.assertEqual(self.login("tenant-b", "b@example.invalid", "secure-pass-b").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/customer/bankroll",
                json={"account_id": "account-b", "entry_type": "deposit", "amount_cents": 100},
                headers=self.origin,
            ).status_code, 403
        )

    def test_bankroll_amount_sign_is_enforced(self):
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200)
        for entry_type, amount in (("deposit", -100), ("withdrawal", 100), ("deposit", 0)):
            with self.subTest(entry_type=entry_type, amount=amount):
                self.assertEqual(
                    self.client.post(
                        "/api/customer/bankroll",
                        json={"account_id": "account-a", "entry_type": entry_type,
                              "amount_cents": amount},
                        headers=self.origin,
                    ).status_code, 422
                )

    def test_customer_session_does_not_grant_operator_access(self):
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200)
        self.assertEqual(self.client.get("/api/accounts").status_code, 401)

    def test_suspended_tenant_revokes_existing_session(self):
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200)
        self.assertEqual(self.client.get("/api/customer/me").status_code, 200)
        with self.connect() as con:
            con.execute("UPDATE customer_tenants SET status='suspended' WHERE id='tenant-a'")
        self.assertEqual(self.client.get("/api/customer/me").status_code, 401)
        self.assertEqual(
            self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 401
        )

    def test_deactivated_user_revokes_existing_session(self):
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200)
        with self.connect() as con:
            con.execute("UPDATE customer_users SET active=0 WHERE id='user-a'")
        self.assertEqual(self.client.get("/api/customer/accounts").status_code, 401)

    def test_customer_cannot_use_operator_cookie(self):
        # A customer token cannot authenticate the legacy operator dashboard.
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)

    def test_logout_revokes_customer_session(self):
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200)
        self.assertEqual(self.client.post("/api/customer/logout", headers=self.origin).status_code, 200)
        self.assertEqual(self.client.get("/api/customer/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
