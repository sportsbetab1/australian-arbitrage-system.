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
        self.patch = patch("app.customer_api.get_db", self.connect)
        self.patch.start()
        self.client = TestClient(app)
        self.origin = {"Origin": "http://testserver"}

    def tearDown(self):
        self.client.close()
        self.patch.stop()
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

    def test_customer_session_does_not_grant_operator_access(self):
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200)
        self.assertEqual(self.client.get("/api/accounts").status_code, 401)

    def test_logout_revokes_customer_session(self):
        self.assertEqual(self.login("tenant-a", "a@example.invalid", "secure-pass-a").status_code, 200)
        self.assertEqual(self.client.post("/api/customer/logout", headers=self.origin).status_code, 200)
        self.assertEqual(self.client.get("/api/customer/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
