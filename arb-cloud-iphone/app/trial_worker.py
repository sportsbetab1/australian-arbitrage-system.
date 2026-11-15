"""Free-tier best-effort, quota-guarded scanner. No bet placement."""
import os
import time
import threading
from datetime import datetime, timezone
from app.odds import fetch_json, build_h2h_candidates
from app.global_scanner import supported_sports, eligible_bookmakers
from scripts.trial_credit_ledger import connect, start, monitor, reserve, reconcile

ALLOWED = ("Sportsbet", "Betfair", "PointsBet", "Unibet", "Dabble", "Picklebet", "Ladbrokes")
INTERVAL = 15 * 60
_started = False
_lock = threading.Lock()
_last_error = None

def enabled():
    return os.getenv("TRIAL_AUTO_SCAN", "").lower() == "true" and bool(os.getenv("ODDS_API_KEY")) and bool(os.getenv("TRIAL_DATABASE_URL"))

def scan_once():
    global _last_error
    if not enabled():
        return
    key = os.environ["ODDS_API_KEY"]
    # Sports catalog is a zero-credit endpoint; do not start the clock if discovery fails.
    catalog, _ = fetch_json(key, "sports", {"all": "false"})
    sports = supported_sports(catalog)
    if not sports:
        _last_error = "No active supported competitions"
        return
    with connect() as con:
        state = monitor(con)
        if state["status"] in ("expired", "stopped", "budget_exhausted"):
            return
        # Reserve a conservative 8 credits per AU h2h request and keep a 50-credit
        # provider-overage buffer. Never attempt a request near the full cap.
        if state["credits_used_or_reserved"] >= 300:
            return
        # Rotate across competitions using the durable request history.
        from scripts.trial_credit_ledger import initialize
        initialize(con)
        rows = con.execute("SELECT sport_key, COUNT(*) FROM trial_requests GROUP BY sport_key").fetchall()
        counts = dict(rows)
        sport = min(sports, key=lambda s: (counts.get(s, 0), s))
        if state["status"] == "not_started":
            start(con)
        request_id = reserve(con, sport)
        if request_id is None:
            return
    try:
        events, usage = fetch_json(key, f"sports/{sport}/odds", {
            "regions": "au", "markets": "h2h", "oddsFormat": "decimal",
            "dateFormat": "iso"
        })
        from app.odds import build_h2h_candidates
        candidates = build_h2h_candidates(eligible_bookmakers(events, ALLOWED))
        qualified = 0
        for candidate in candidates:
            outcomes = candidate["outcomes"]
            if len(outcomes) != 2:
                continue
            effective = [1 + (float(x["odds"]) - 1) * (1 - float(x["commission_pct"]) / 100) for x in outcomes]
            if all(x > 1 for x in effective) and (1 / sum(1 / x for x in effective) - 1) * 100 >= 2:
                qualified += 1
        cost = usage.get("request_cost")
        # Unknown cost: keep full reservation. Abnormally high cost: stop further scanning.
        with connect() as con:
            reconcile(con, request_id, cost, qualified, "completed")
            if cost is not None and cost > 8:
                from scripts.trial_credit_ledger import stop
                stop(con)
                _last_error = "Unexpected provider request cost; scanner paused"
    except Exception as exc:
        _last_error = type(exc).__name__
        with connect() as con:
            reconcile(con, request_id, None, 0, "provider_error")

def _loop():
    global _last_error
    while enabled():
        try:
            scan_once()
        except Exception as exc:
            _last_error = type(exc).__name__
        time.sleep(INTERVAL)

def start_worker():
    global _started
    with _lock:
        if _started or not enabled():
            return
        _started = True
        threading.Thread(target=_loop, name="trial-odds-worker", daemon=True).start()

def worker_info():
    return {"scanner_enabled": enabled(), "last_error": _last_error, "interval_minutes": 15}
