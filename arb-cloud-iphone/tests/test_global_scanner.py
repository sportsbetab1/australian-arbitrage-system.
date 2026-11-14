"""Offline regression tests for worldwide scanner safety controls."""
import sqlite3
from datetime import datetime, timedelta, timezone

from app.global_scanner import (
    SCAN_CONFIG, AUTOMATED_LIMIT, available_automated_points,
    billing_week_start, eligible_bookmakers, reserve_scan, reconcile_scan,
    supported_sports,
)


def test_configuration():
    assert SCAN_CONFIG["mode"] == "adaptive"
    assert SCAN_CONFIG["automated_budget_percent"] == 75
    assert SCAN_CONFIG["minimum_net_roi_percent"] == 2
    assert SCAN_CONFIG["regions"] == ("au", "uk", "eu", "us")
    assert SCAN_CONFIG["event_window_hours"] == 24
    assert SCAN_CONFIG["minimum_repeat_interval_minutes"] == 5


def test_discovery_and_bookmakers():
    assert supported_sports([
        {"key": "soccer_epl", "active": True},
        {"key": "basketball_nba", "active": True},
        {"key": "politics_us_presidential_election", "active": True},
        {"key": "tennis_atp", "active": False},
    ]) == ["basketball_nba", "soccer_epl"]
    events = [{"id": "a", "bookmakers": [
        {"title": "Sportsbet"}, {"title": "Unknown"}
    ]}]
    result = eligible_bookmakers(events, ["Sportsbet"])
    assert len(result[0]["bookmakers"]) == 1
    assert events[0]["bookmakers"][1]["title"] == "Unknown"


def test_pacing_and_protected_reserve():
    assert available_automated_points(0, 0) == 0
    assert available_automated_points(0, 7 * 86400) == AUTOMATED_LIMIT
    assert available_automated_points(AUTOMATED_LIMIT, 7 * 86400) == 0
    assert available_automated_points(100000, 3600) == 0


def test_durable_reservations_and_cooldown():
    con = sqlite3.connect(":memory:")
    at = billing_week_start(datetime(2026, 11, 18, tzinfo=timezone.utc)) + timedelta(hours=1)
    con.execute("BEGIN IMMEDIATE")
    first, error = reserve_scan(con, "soccer_epl", at)
    con.commit()
    assert first and error is None
    con.execute("BEGIN IMMEDIATE")
    second, error = reserve_scan(con, "soccer_epl", at + timedelta(minutes=4))
    con.commit()
    assert second is None and error == "cooldown"
    con.execute("BEGIN IMMEDIATE")
    third, error = reserve_scan(con, "soccer_epl", at + timedelta(minutes=6))
    con.commit()
    assert third and error is None
    reconcile_scan(con, first, 8, "completed")
    con.commit()
    assert con.execute(
        "SELECT MAX(reserved_points, actual_points) FROM global_scan_ledger WHERE id=?",
        (first,),
    ).fetchone()[0] == 8


def test_quota_rejects_large_reservation():
    con = sqlite3.connect(":memory:")
    at = datetime(2026, 11, 18, tzinfo=timezone.utc)
    con.execute("BEGIN IMMEDIATE")
    scan_id, reason = reserve_scan(con, "soccer_epl", at, estimated_points=1_500_001)
    con.commit()
    assert scan_id is None and reason == "quota_or_pacing"
