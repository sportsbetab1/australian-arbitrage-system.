"""Offline tests for one-time 350-credit trial safety."""
import sqlite3
from datetime import datetime, timedelta, timezone

from app.global_scanner import reserve_trial_scan, trial_status, reconcile_scan


def test_trial_limit_cooldown_and_expiry():
    con = sqlite3.connect(":memory:")
    now = datetime(2026, 11, 14, 8, tzinfo=timezone.utc)
    con.execute("BEGIN IMMEDIATE")
    first, error = reserve_trial_scan(con, "soccer_epl", now)
    con.commit()
    assert first and error is None
    assert trial_status(con, now)["remaining"] == 349
    con.execute("BEGIN IMMEDIATE")
    blocked, error = reserve_trial_scan(con, "soccer_epl", now + timedelta(minutes=1))
    con.commit()
    assert blocked is None and error == "cooldown"
    reconcile_scan(con, first, 3, "completed")
    con.commit()
    assert trial_status(con, now)["remaining"] == 347
    for index in range(347):
        at = now + timedelta(minutes=6)
        con.execute("BEGIN IMMEDIATE")
        scan_id, error = reserve_trial_scan(con, f"soccer_{index}", at)
        con.commit()
        assert scan_id and error is None
    assert trial_status(con, now + timedelta(minutes=7))["remaining"] == 0
    con.execute("BEGIN IMMEDIATE")
    blocked, error = reserve_trial_scan(con, "soccer_extra", now + timedelta(minutes=8))
    con.commit()
    assert blocked is None and error == "trial_budget_exhausted"
    con.execute("BEGIN IMMEDIATE")
    blocked, error = reserve_trial_scan(con, "soccer_extra", now + timedelta(hours=24))
    con.commit()
    assert blocked is None and error == "trial_expired"


def test_trial_does_not_restart_after_expiry():
    con = sqlite3.connect(":memory:")
    start = datetime(2026, 11, 14, tzinfo=timezone.utc)
    con.execute("BEGIN IMMEDIATE")
    assert reserve_trial_scan(con, "basketball_nba", start)[0]
    con.commit()
    assert trial_status(con, start + timedelta(days=2))["active"] is False
