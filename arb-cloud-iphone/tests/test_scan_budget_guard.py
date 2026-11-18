"""Regression coverage for weekly scanner accounting and quota safety."""
import sqlite3
import unittest
from datetime import datetime, timezone

from app.global_scanner import (
    AUTOMATED_LIMIT,
    WEEK_SECONDS,
    available_automated_points,
    reconcile_scan,
)


class GlobalScanBudgetTests(unittest.TestCase):
    def test_automated_budget_is_paced(self):
        self.assertEqual(available_automated_points(0, 0), 0)
        self.assertEqual(available_automated_points(0, WEEK_SECONDS / 2), AUTOMATED_LIMIT // 2)
        self.assertEqual(available_automated_points(0, WEEK_SECONDS), AUTOMATED_LIMIT)

    def test_usage_cannot_borrow_future_allowance(self):
        self.assertEqual(available_automated_points(750000, WEEK_SECONDS / 2), 0)
        self.assertEqual(available_automated_points(800000, WEEK_SECONDS / 2), 0)
        self.assertEqual(available_automated_points(0, WEEK_SECONDS * 2), AUTOMATED_LIMIT)

    def test_fractional_actual_cost_rounds_up(self):
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE TABLE global_scan_ledger (id INTEGER PRIMARY KEY, actual_points INTEGER, status TEXT)")
            con.execute("INSERT INTO global_scan_ledger(id,actual_points,status) VALUES (1,NULL,'reserved')")
            reconcile_scan(con, 1, 4.1, "completed")
            self.assertEqual(con.execute("SELECT actual_points FROM global_scan_ledger WHERE id=1").fetchone()[0], 5)
        finally:
            con.close()

    def test_nonfinite_cost_rejected(self):
        con = sqlite3.connect(":memory:")
        try:
            for value in (float("nan"), float("inf"), float("-inf"), -1):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    reconcile_scan(con, 1, value, "completed")
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
