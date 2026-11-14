"""HTTP boundary tests; no real bookmaker calls or account data are used."""
import unittest
from fastapi.testclient import TestClient

from app.main import app


class AuthenticationBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Do not use a TestClient context manager: startup would initialize the
        # application database. These tests only exercise request middleware.
        cls.client = TestClient(app)

    def test_health_is_public(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "manual-only")

    def test_dashboard_rejects_anonymous_request(self):
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("cache-control"), "no-store, private")

    def test_accounts_reject_anonymous_request(self):
        response = self.client.get("/api/accounts")
        self.assertEqual(response.status_code, 401)

    def test_positions_reject_anonymous_request(self):
        response = self.client.get("/api/positions")
        self.assertEqual(response.status_code, 401)

    def test_mutation_without_origin_is_forbidden(self):
        response = self.client.post("/api/scan", json={})
        self.assertEqual(response.status_code, 403)

    def test_cross_origin_mutation_is_forbidden(self):
        response = self.client.post("/api/scan", json={},
                                    headers={"Origin": "https://attacker.example"})
        self.assertEqual(response.status_code, 403)

    def test_same_origin_mutation_still_requires_session(self):
        response = self.client.post("/api/scan", json={},
                                    headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 401)

    def test_security_headers_are_present(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(response.headers.get("x-frame-options"), "DENY")
        self.assertEqual(response.headers.get("referrer-policy"),
                         "strict-origin-when-cross-origin")

    def test_dashboard_shell_not_cacheable(self):
        response = self.client.get("/")
        self.assertEqual(response.headers.get("cache-control"), "no-store, private")

    def test_each_navigation_page_can_be_opened_directly(self):
        for path in ("/dashboard", "/positions", "/settlements", "/accounts",
                     "/scanner", "/analytics", "/settings", "/notifications"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('id="authGate"', response.text)
                self.assertEqual(response.headers.get("cache-control"), "no-store, private")

    def test_unknown_page_still_returns_not_found(self):
        self.assertEqual(self.client.get("/not-a-dashboard-page").status_code, 404)


if __name__ == "__main__":
    unittest.main()
