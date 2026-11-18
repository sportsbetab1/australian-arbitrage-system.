"""Regression tests for the 350-credit trial's conservative reservation ceiling."""
import unittest

from app.trial_worker import trial_scan_allowed


class TrialQuotaGuardTests(unittest.TestCase):
    def test_last_safe_reservation(self):
        self.assertTrue(trial_scan_allowed({"status": "active", "credits_used_or_reserved": 292}))

    def test_next_reservation_cannot_cross_300(self):
        self.assertFalse(trial_scan_allowed({"status": "active", "credits_used_or_reserved": 293}))

    def test_trial_not_started_has_budget(self):
        self.assertTrue(trial_scan_allowed({"status": "not_started", "credits_used_or_reserved": 0}))

    def test_stopped_and_expired_never_scan(self):
        for status in ("stopped", "expired", "budget_exhausted", "database_unavailable"):
            with self.subTest(status=status):
                self.assertFalse(trial_scan_allowed({"status": status, "credits_used_or_reserved": 0}))

    def test_unknown_usage_fails_closed(self):
        self.assertFalse(trial_scan_allowed({"status": "active"}))


if __name__ == "__main__":
    unittest.main()
