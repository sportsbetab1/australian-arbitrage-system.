"""Risk-policy regression tests for the live FastAPI application.

Run from arb-cloud-iphone with dependencies installed:
    python -m unittest discover -s tests -p 'test_risk_policy.py' -v
"""
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.main import (
    Candidate, Outcome, PositionLeg, calculate, calculate_position_roi,
    check_new_position_allowed, MAX_EXPOSURE, MIN_NET_ROI,
    MAX_OPEN_POSITIONS, SETTLEMENT_BUFFER_LIMIT, BOOKMAKERS,
    require_qualified_open_roi,
)


class FakeCursor:
    def __init__(self, total):
        self.total = total

    def fetchone(self):
        return {"total": self.total}


class FakeConnection:
    def __init__(self, unmatched=0):
        self.unmatched = unmatched

    def execute(self, query, params=()):
        if "status = 'Unmatched'" in query:
            return FakeCursor(self.unmatched)
        raise AssertionError("Unexpected SQL in mocked risk test: " + query)


class RiskPolicyTests(unittest.TestCase):
    def test_qualified_open_position_roi(self):
        self.assertIsNone(require_qualified_open_roi(0.015))
        self.assertIsNone(require_qualified_open_roi(0.08))

    def test_below_floor_position_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            require_qualified_open_roi(0.0149)
        self.assertEqual(error.exception.status_code, 409)

    def test_unmatched_accepted_leg_can_be_recorded_for_recovery(self):
        self.assertIsNone(require_qualified_open_roi(-0.50, allow_unmatched=True))

    def test_exposure_cap_and_buffer_policy(self):
        self.assertEqual(MAX_EXPOSURE, 7500.0)
        self.assertEqual(SETTLEMENT_BUFFER_LIMIT, 1000.0)
        self.assertEqual(MIN_NET_ROI, 0.015)
        self.assertIsNone(MAX_OPEN_POSITIONS)
        self.assertNotIn("TAB", BOOKMAKERS)

    def test_exposure_at_limit_is_allowed(self):
        with patch("app.main.get_buffer_state", return_value={"overdue_items": 0}), \
             patch("app.main.get_open_exposure", return_value=7000.0):
            self.assertIsNone(check_new_position_allowed(FakeConnection(), 500.0))

    def test_exposure_over_limit_is_rejected(self):
        with patch("app.main.get_buffer_state", return_value={"overdue_items": 0}), \
             patch("app.main.get_open_exposure", return_value=7000.0):
            with self.assertRaises(HTTPException) as error:
                check_new_position_allowed(FakeConnection(), 500.01)
            self.assertEqual(error.exception.status_code, 409)

    def test_unmatched_position_blocks_new_position(self):
        with patch("app.main.get_buffer_state", return_value={"overdue_items": 0}):
            with self.assertRaises(HTTPException) as error:
                check_new_position_allowed(FakeConnection(unmatched=1), 100.0)
            self.assertEqual(error.exception.status_code, 409)

    def test_overdue_buffer_blocks_new_position(self):
        with patch("app.main.get_buffer_state", return_value={"overdue_items": 1}):
            with self.assertRaises(HTTPException) as error:
                check_new_position_allowed(FakeConnection(), 100.0)
            self.assertEqual(error.exception.status_code, 409)

    def test_binary_position_roi_uses_worst_case(self):
        legs = [
            PositionLeg(account_id=1, event_id="e1", market_id="m1",
                        outcome_id="home", selection="Home", odds=2.2, stake=50),
            PositionLeg(account_id=8, event_id="e1", market_id="m1",
                        outcome_id="away", selection="Away", odds=2.2, stake=50),
        ]
        result = calculate_position_roi(legs, ["home", "away"], {1: 0, 8: 0})
        self.assertAlmostEqual(result["total_stake"], 100)
        self.assertAlmostEqual(result["expected_profit"], 10)
        self.assertAlmostEqual(result["roi"], 0.10)

    def test_commission_reduces_worst_case_profit(self):
        legs = [
            PositionLeg(account_id=1, event_id="e1", market_id="m1",
                        outcome_id="home", selection="Home", odds=2.2, stake=50),
            PositionLeg(account_id=8, event_id="e1", market_id="m1",
                        outcome_id="away", selection="Away", odds=2.2, stake=50),
        ]
        result = calculate_position_roi(legs, ["home", "away"], {1: 6, 8: 0})
        self.assertAlmostEqual(result["expected_profit"], 6.4)
        self.assertAlmostEqual(result["roi"], 0.064)

    def test_duplicate_outcomes_are_rejected(self):
        with self.assertRaises(ValueError):
            Candidate(sport="Soccer", event="Test", market="Winner", total_stake=100,
                      outcomes=[
                          Outcome(selection="Home", bookmaker="Sportsbet", odds=2.1),
                          Outcome(selection="home", bookmaker="Ladbrokes", odds=2.1),
                      ])


if __name__ == "__main__":
    unittest.main()
