"""End-to-end customer HTTP isolation tests against the mounted FastAPI app.

Run from arb-cloud-iphone with isolated DB_PATH. Never run on production DB.
"""
import os
import tempfile
import unittest
from pathlib import Path

# DB_PATH is read during imports; set before importing app.
_TEST_DIR = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(_TEST_DIR.name) / "customer-test.sqlite3")
os.environ["AUTH_COOKIE_SECURE"] = "false"

from fastapi.testclient import TestClient
from app.main import app, initialise_system
from app.database import get_db
from app.commercial_security import create_user

class CustomerHTTPIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialise_system()
        cls.client = TestClient(app, base_url="http://testserver")
        with get_db() as con:
            for tenant in ("tenant-a","tenant-b"):
                con.execute("INSERT INTO commercial_tenants(id,display_name,kind,status) VALUES (?,?,'customer','active')",(tenant,tenant))
            for tenant, email in (("tenant-a","a@example.com"),("tenant-b","b@example.com")):
                uid=create_user(con,email,"correct-long-password")
                con.execute("UPDATE commercial_users SET email_verified_at=CURRENT_TIMESTAMP WHERE id=?",(uid,))
                con.execute("INSERT INTO commercial_memberships(tenant_id,user_id,role) VALUES (?,?,'tenant_admin')",(tenant,uid))
                con.execute("INSERT INTO commercial_customer_accounts(id,tenant_id,label,bookmaker) VALUES (?,?,?,?)",
                            (tenant+"-account",tenant,tenant,"Sportsbet"))
                con.execute("INSERT INTO commercial_customer_positions(id,tenant_id,event,sport,market) VALUES (?,?,?,?,?)",
                            (tenant+"-position",tenant,tenant,"Soccer","H2H"))
                con.execute("INSERT INTO commercial_customer_bankroll_ledger(id,tenant_id,entry_type,amount_cents) VALUES (?,?,'deposit',1000)",
                            (tenant+"-ledger",tenant))

    def test_login_and_cross_tenant_http_denial(self):
        headers={"Origin":"http://testserver"}
        with TestClient(app,base_url="http://testserver") as client:
            login=client.post("/api/customer/login",json={"email":"a@example.com","password":"correct-long-password"},headers=headers)
            self.assertEqual(login.status_code,200,login.text)
            own = client.get("/api/customer/workspaces/tenant-a/positions")
            self.assertEqual(own.status_code,200)
            self.assertEqual(len(own.json()),1)
            self.assertEqual(own.json()[0]["tenant_id"],"tenant-a")
            self.assertEqual(client.get("/api/customer/workspaces/tenant-b/positions").status_code,404)
            self.assertEqual(client.get("/api/customer/workspaces/tenant-b/bankroll").status_code,404)
            self.assertEqual(client.get("/api/customer/workspaces/tenant-b/accounts").status_code,404)
            self.assertEqual(client.get("/api/positions").status_code,401)
            logout=client.post("/api/customer/logout",headers=headers)
            self.assertEqual(logout.status_code,200)
            self.assertEqual(client.get("/api/customer/workspaces/tenant-a/positions").status_code,401)

if __name__ == "__main__":
    unittest.main()
