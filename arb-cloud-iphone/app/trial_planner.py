"""Zero-credit adaptive trial planner. This module never requests paid odds.

Run discovery via /sports and /sports/{key}/events before choosing paid scans.
The caller must use the durable trial ledger and verify provider costs.
"""
from datetime import datetime, timezone, timedelta

from app.global_scanner import supported_sports, SCAN_CONFIG


def rank_competitions(catalog, schedules, at=None):
    """Rank eligible competitions by upcoming event density, then urgency.

    schedules maps sport key to a list of provider event dictionaries.
    It is safe to call with missing/invalid schedules; no API calls occur.
    """
    at = at or datetime.now(timezone.utc)
    deadline = at + timedelta(hours=SCAN_CONFIG["event_window_hours"])
    ranked = []
    for key in supported_sports(catalog):
        upcoming = []
        for event in schedules.get(key, []):
            if not isinstance(event, dict):
                continue
            try:
                start = datetime.fromisoformat(str(event["commence_time"]).replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError):
                continue
            if start.tzinfo is not None and at < start <= deadline:
                upcoming.append(start)
        if not upcoming:
            continue
        nearest_minutes = min((start - at).total_seconds() / 60 for start in upcoming)
        # Event density and imminence, not arbitrary country popularity.
        score = len(upcoming) * 10 + 120 / (30 + nearest_minutes)
        ranked.append({"sport_key": key, "upcoming_events": len(upcoming),
                       "nearest_event_minutes": round(nearest_minutes, 1),
                       "priority_score": round(score, 3)})
    return sorted(ranked, key=lambda row: (-row["priority_score"], row["sport_key"]))


def trial_scan_plan(catalog, schedules, remaining_credits, at=None):
    """Generate a bounded, AU-first candidate queue, not a spending command."""
    if not isinstance(remaining_credits, int) or remaining_credits < 0:
        raise ValueError("remaining_credits must be non-negative integer")
    return rank_competitions(catalog, schedules, at)[:min(remaining_credits, 350)]
