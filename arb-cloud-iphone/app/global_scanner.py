"""Global odds discovery and budget-aware scanning helpers.

No bets are submitted by this module. Provider coverage is not country coverage:
sport keys identify competitions, and regions identify bookmaker markets.
"""
from datetime import datetime, timezone
import re

SPORT_PREFIXES = ("soccer_", "tennis_", "basketball_", "rugbyleague_", "aussierules_", "cricket_")
SPORT_EXACT = {"basketball_nba", "rugbyleague_nrl", "aussierules_afl"}
REGIONS = ("au", "uk", "eu", "us")
WEEKLY_TOTAL = 2_000_000
AUTOMATED_LIMIT = 1_500_000
WEEK_SECONDS = 7 * 24 * 3600
KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")


def supported_sports(catalog):
    """Return active provider competition keys across the configured sports."""
    return sorted({
        entry["key"] for entry in catalog
        if isinstance(entry, dict)
        and isinstance(entry.get("key"), str)
        and KEY_PATTERN.fullmatch(entry["key"])
        and (entry["key"].startswith(SPORT_PREFIXES) or entry["key"] in SPORT_EXACT)
        and entry.get("active", True)
    })


def available_automated_points(used, elapsed_seconds):
    """Evenly pace the 75% allowance; never borrow from the protected reserve."""
    elapsed = max(0, min(WEEK_SECONDS, float(elapsed_seconds)))
    accrued = int(AUTOMATED_LIMIT * elapsed / WEEK_SECONDS)
    return max(0, min(AUTOMATED_LIMIT - max(0, int(used)), accrued - max(0, int(used))))


def eligible_bookmakers(events, allowed):
    """Remove odds from accounts we cannot use; preserve provider event structure."""
    names = {name.casefold().strip() for name in allowed if isinstance(name, str)}
    filtered = []
    for event in events:
        if not isinstance(event, dict):
            continue
        matching = [
            bookmaker for bookmaker in event.get("bookmakers", [])
            if isinstance(bookmaker, dict)
            and str(bookmaker.get("title", "")).casefold().strip() in names
        ]
        if matching:
            filtered.append({**event, "bookmakers": matching})
    return filtered
