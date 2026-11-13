"""Static frontend/backend contract checks. Run: python -m unittest discover -s tests -p 'test_frontend_contract.py'"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "static" / "index.html"
BACKEND = ROOT / "app" / "main.py"


class FrontendBackendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = FRONTEND.read_text(encoding="utf-8")
        cls.backend = BACKEND.read_text(encoding="utf-8")

    def test_dashboard_elements_are_present(self):
        for element in (
            "bankroll", "exposure", "active", "capacity", "maximumExposure",
            "protectedReserve", "settledPositions", "realisedProfit",
            "reinvestedProfit", "withdrawalAllocation", "bufferSummary",
        ):
            with self.subTest(element=element):
                self.assertRegex(self.html, r'id=["\']' + element + r'["\']')

    def test_dashboard_fields_match_backend(self):
        dashboard = self.backend.split('@app.get("/api/dashboard")', 1)[1].split(
            '# OPPORTUNITY SCANNER', 1
        )[0]
        for field in (
            "starting_bankroll", "maximum_exposure", "open_exposure",
            "remaining_exposure", "open_positions", "settled_positions",
            "realised_profit_loss", "reinvested_profit",
            "withdrawal_allocation", "buffer",
        ):
            with self.subTest(field=field):
                self.assertIn('"' + field + '"', dashboard)
                self.assertIn('d.' + field, self.html)

    def test_frontend_fetch_routes_exist(self):
        routes = set(re.findall(r"api\('(/[^']+)'", self.html))
        routes.update(re.findall(r'api\("(/[^"]+)"', self.html))
        backend_routes = set(re.findall(r'@app\.(?:get|post|put|patch|delete)\("(/api/[^"]+)"\)', self.backend))
        for route in routes:
            if route.endswith("/") or "+" in route or "${" in route:
                continue  # Parameterized paths require runtime tests.
            with self.subTest(route=route):
                self.assertIn("/api" + route, backend_routes)

    def test_secondary_services_cannot_block_core_dashboard(self):
        self.assertIn("Promise.allSettled", self.html)
        self.assertIn("api('/dashboard')", self.html)
        self.assertIn("api('/reports/summary')", self.html)
        self.assertIn("some secondary services unavailable", self.html)

    def test_dark_theme_has_accessible_focus_indicators(self):
        self.assertIn("--app-bg:#0A1425", self.html)
        self.assertIn("color-scheme:dark", self.html)
        self.assertIn("outline:3px solid #8EE6FF", self.html)

    def test_buffer_pause_is_visible(self):
        self.assertIn("new_positions_paused", self.html)
        self.assertIn("New positions paused", self.html)
        self.assertIn("b.overdue_items", self.html)

    def test_session_expiry_stops_polling(self):
        self.assertIn("if(refreshTimer){clearInterval(refreshTimer);refreshTimer=null}", self.html)
        self.assertIn("if(refreshInProgress||!currentUser)return", self.html)

    def test_exposure_governs_concurrency(self):
        self.assertIn("MAX_OPEN_POSITIONS = None", self.backend)
        self.assertNotIn("Open-position limit reached", self.backend)
        self.assertIn("if proposed > MAX_EXPOSURE:", self.backend)

    def test_removed_bookmaker_not_in_supported_list(self):
        bookmaker_block = self.backend.split("BOOKMAKERS = [", 1)[1].split("]", 1)[0]
        self.assertNotIn('"TAB"', bookmaker_block)

    def test_no_hardcoded_financial_dashboard_values(self):
        for element in ("bankroll", "exposure", "active", "capacity"):
            with self.subTest(element=element):
                self.assertRegex(self.html, r'id="' + element + r'">—</div>')

    def test_manual_betting_notice_remains(self):
        self.assertIn("MANUAL BETTING", self.html)
        self.assertIn("This app does not open bookmaker sites or place bets.", self.html)


if __name__ == "__main__":
    unittest.main()
