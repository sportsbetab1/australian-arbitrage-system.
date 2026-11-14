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

# Runtime configuration is intentionally immutable. Operator changes require review.
SCAN_CONFIG = {
    "mode": "adaptive",
    "automated_budget_percent": 75,
    "minimum_net_roi_percent": 2.0,
    "regions": REGIONS,
    "event_window_hours": 24,
    "minimum_repeat_interval_minutes": 5,
}
RESERVATION_POINTS = len(REGIONS)  # one h2h market in each region


def billing_week_start(at):
    """UTC Monday is a conservative default; align with provider billing separately."""
    at = at.astimezone(timezone.utc)
    monday = at.replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    return monday - timedelta(days=monday.weekday())


def reserve_scan(con, sport_key, now, estimated_points=RESERVATION_POINTS):
    """Atomic, durable quota reservation. Caller must BEGIN IMMEDIATE and commit.

    Reservation remains charged even if the provider times out; never refund
    uncertain requests. Usage reconciliation can increase, never decrease, cost.
    """
    from datetime import timedelta
    if not KEY_PATTERN.fullmatch(sport_key):
        raise ValueError("Invalid competition key")
    week = billing_week_start(now).isoformat()
    con.execute("""CREATE TABLE IF NOT EXISTS global_scan_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week_start TEXT NOT NULL,
        sport_key TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        reserved_points INTEGER NOT NULL,
        actual_points INTEGER,
        status TEXT NOT NULL
    )""")
    last = con.execute(
        "SELECT requested_at FROM global_scan_ledger WHERE sport_key=? ORDER BY id DESC LIMIT 1",
        (sport_key,),
    ).fetchone()
    if last:
        previous = datetime.fromisoformat(last[0])
        if now - previous < timedelta(minutes=SCAN_CONFIG["minimum_repeat_interval_minutes"]):
            return None, "cooldown"
    used = con.execute(
        "SELECT COALESCE(SUM(MAX(reserved_points, COALESCE(actual_points, 0))), 0) "
        "FROM global_scan_ledger WHERE week_start=?", (week,),
    ).fetchone()[0]
    elapsed = (now - billing_week_start(now)).total_seconds()
    if available_automated_points(used, elapsed) < estimated_points:
        return None, "quota_or_pacing"
    cursor = con.execute(
        "INSERT INTO global_scan_ledger(week_start,sport_key,requested_at,reserved_points,status) "
        "VALUES (?,?,?,?,?)", (week, sport_key, now.isoformat(), estimated_points, "reserved"),
    )
    return cursor.lastrowid, None


def reconcile_scan(con, scan_id, actual_points, status):
    """Keep maximum of reserved and reported cost to fail safely."""
    if actual_points is not None and (not isinstance(actual_points, (int, float)) or actual_points < 0):
        raise ValueError("Invalid provider cost")
    con.execute(
        "UPDATE global_scan_ledger SET actual_points=?,status=? WHERE id=?",
        (None if actual_points is None else int(actual_points), status, scan_id),
    )
