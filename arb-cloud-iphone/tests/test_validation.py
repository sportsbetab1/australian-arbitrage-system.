import unittest
from unittest.mock import patch
from pydantic import ValidationError
from app import main


class ValidationTests(unittest.TestCase):
    def candidate(self, **changes):
        data = dict(sport="Tennis", event="A v B", market="Winner",
                    total_stake=500, outcomes=[dict(selection=s, bookmaker=s, odds=2.1)
                                              for s in ["A", "B"]])
        data.update(changes)
        return main.Candidate(**data)

    def test_nonfinite_numbers_rejected(self):
        for value in [float("nan"), float("inf"), -float("inf")]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    main.SettleSet(actual_pnl=value)
                with self.assertRaises(ValidationError):
                    main.Outcome(selection="A", bookmaker="B", odds=value)

    def test_blank_and_duplicate_selections_rejected(self):
        with self.assertRaises(ValidationError):
            self.candidate(event="  ")
        with self.assertRaises(ValidationError):
            self.candidate(outcomes=[dict(selection=s, bookmaker="X", odds=2.1)
                                     for s in ["Home", " home "]])

    def test_fractional_cents_rejected(self):
        with self.assertRaises(ValidationError):
            self.candidate(total_stake=123.456)

    def test_confirmation_requires_boolean(self):
        for value in ["yes", 1, "true"]:
            with self.assertRaises(ValidationError):
                main.OpenSet(opportunity_id="x", manual_confirmation=value)

    def test_rounding_can_disqualify_theoretical_arbitrage(self):
        candidate = self.candidate(total_stake=0.03)
        result = main.calculate(candidate)
        self.assertGreater(result["roi"], main.MIN_NET_ROI)
        self.assertLess(result["net_roi"], main.MIN_NET_ROI)
        self.assertFalse(main.scan(candidate)["qualified"])

    def test_commission_and_stakes(self):
        candidate = self.candidate(total_stake=123.45)
        candidate.outcomes[0].commission_pct = 5
        result = main.calculate(candidate)
        self.assertAlmostEqual(sum(result["stakes"]), 123.45)
        self.assertAlmostEqual(result["adjusted_odds"][0], 2.045)
        self.assertAlmostEqual(result["net_roi"], result["profit"] / 123.45)
