import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "arb-ledger-tests.db")
from fastapi import HTTPException
from app import main


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_path = main.DB_PATH
        main.DB_PATH = os.path.join(self.temp.name, "ledger.db")
        main.init_db()

    def tearDown(self):
        main.DB_PATH = self.previous_path
        self.temp.cleanup()

    def candidate(self, stake=125):
        candidate = main.Candidate(
            sport="Tennis", event="A v B", market="Winner", total_stake=stake,
            outcomes=[main.Outcome(selection=x, bookmaker=x, odds=2.1)
                      for x in ["A", "B"]])
        result = main.calculate(candidate)
        # Seed the ledger without invoking external notification services.
        import json
        import uuid
        opportunity_id = str(uuid.uuid4())
        with main.db() as con:
            con.execute("INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (opportunity_id, main.now(), candidate.sport, candidate.event,
                         candidate.market, json.dumps([o.model_dump() for o in candidate.outcomes]),
                         result["sum_inverse"], result["roi"], json.dumps(result["stakes"]), "Candidate"))
        return main.OpenSet(opportunity_id=opportunity_id, manual_confirmation=True), result

    def test_actual_stake_and_rounded_profit(self):
        req, result = self.candidate(123.45)
        main.open_set(req)
        ledger = main.sets()
        self.assertEqual(ledger["open_exposure"], 123.45)
        self.assertAlmostEqual(ledger["items"][0]["expected_profit"], result["profit"])

    def test_duplicate_stays_rejected_after_settlement(self):
        req, _ = self.candidate()
        opened = main.open_set(req)
        main.settle(opened["set_id"], main.SettleSet(actual_pnl=5))
        with self.assertRaises(HTTPException) as error:
            main.open_set(req)
        self.assertEqual(error.exception.status_code, 409)

    def test_exposure_limit_uses_submitted_amount(self):
        from unittest.mock import patch
        req, _ = self.candidate(125)
        with patch.object(main, "MAX_OPEN_EXPOSURE", 150):
            main.open_set(req)
            another, _ = self.candidate(30)
            with self.assertRaises(HTTPException) as error:
                main.open_set(another)
            self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(main.sets()["open_exposure"], 125)

    def test_concurrent_requests_respect_capacity(self):
        requests = [self.candidate()[0] for _ in range(10)]
        def attempt(req):
            try:
                main.open_set(req)
                return True
            except HTTPException as error:
                self.assertEqual(error.status_code, 409)
                return False
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(attempt, requests))
        self.assertEqual(sum(results), 5)
        self.assertEqual(main.sets()["open_exposure"], 625)

    def test_concurrent_duplicate_creates_one_set(self):
        req, _ = self.candidate()
        def attempt(_):
            try:
                main.open_set(req)
                return True
            except HTTPException as error:
                self.assertEqual(error.status_code, 409)
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(attempt, range(4))), 1)
        self.assertEqual(main.sets()["open_sets"], 1)


if __name__ == "__main__":
    unittest.main()
