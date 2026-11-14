"""Deterministic tests for the zero-credit competition planner."""
from datetime import datetime, timedelta, timezone
from app.trial_planner import rank_competitions, trial_scan_plan


def test_only_upcoming_supported_events():
    now = datetime(2026, 11, 14, 9, tzinfo=timezone.utc)
    catalog = [{"key": "soccer_epl"}, {"key": "basketball_nba"},
               {"key": "politics_us_presidential_election"}]
    def event(hours):
        return {"commence_time": (now + timedelta(hours=hours)).isoformat()}
    schedules = {
        "soccer_epl": [event(1), event(2), event(40)],
        "basketball_nba": [event(3)],
        "politics_us_presidential_election": [event(1)]}
    plan = rank_competitions(catalog, schedules, now)
    assert [row["sport_key"] for row in plan] == ["soccer_epl", "basketball_nba"]
    assert plan[0]["upcoming_events"] == 2


def test_zero_budget_means_no_paid_scans():
    now = datetime(2026, 11, 14, 9, tzinfo=timezone.utc)
    catalog = [{"key": "soccer_epl"}]
    schedules = {"soccer_epl": [{"commence_time": (now + timedelta(hours=1)).isoformat()}]}
    assert trial_scan_plan(catalog, schedules, 0, now) == []
    assert len(trial_scan_plan(catalog, schedules, 1, now)) == 1
