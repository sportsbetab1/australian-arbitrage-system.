"""PostgreSQL-backed, fail-closed trial quota and monitoring.

This is independent of the preview app's ephemeral SQLite database.
Requires TRIAL_DATABASE_URL and psycopg. No paid odds calls are made here.
"""
import os
from datetime import datetime, timedelta, timezone

LIMIT = 350
DURATION = timedelta(hours=24)
RESERVATION = 4  # Conservative per-request hold; reconcile with actual provider cost.


def connect():
    import psycopg
    url = os.environ.get("TRIAL_DATABASE_URL")
    if not url:
        raise RuntimeError("TRIAL_DATABASE_URL is required; paid scanning disabled")
    return psycopg.connect(url, sslmode="require")


def initialize(con):
    con.execute("""CREATE TABLE IF NOT EXISTS trial_control (
        id INTEGER PRIMARY KEY CHECK (id=1),
        started_at TIMESTAMPTZ NOT NULL,
        ends_at TIMESTAMPTZ NOT NULL,
        stopped BOOLEAN NOT NULL DEFAULT FALSE,
        max_credits INTEGER NOT NULL CHECK(max_credits=350)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS trial_requests (
        id BIGSERIAL PRIMARY KEY,
        sport_key TEXT NOT NULL,
        reserved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        reserved_credits INTEGER NOT NULL CHECK(reserved_credits>0),
        actual_credits INTEGER,
        status TEXT NOT NULL DEFAULT 'reserved',
        qualified_count INTEGER NOT NULL DEFAULT 0
    )""")


def start(con, at=None):
    at = at or datetime.now(timezone.utc)
    with con.transaction():
        initialize(con)
        con.execute("SELECT pg_advisory_xact_lock(350024)")
        con.execute(
            "INSERT INTO trial_control(id,started_at,ends_at,max_credits) "
            "VALUES (1,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (at, at + DURATION, LIMIT),
        )
        return monitor(con, at)


def monitor(con, at=None):
    at = at or datetime.now(timezone.utc)
    initialize(con)
    row = con.execute("SELECT started_at,ends_at,stopped,max_credits FROM trial_control WHERE id=1").fetchone()
    if row is None:
        return {"status": "not_started", "credits_used_or_reserved": 0,
                "credits_remaining": LIMIT, "scan_count": 0, "qualified_count": 0}
    started, ends, stopped, limit = row
    usage = con.execute("""SELECT COALESCE(SUM(GREATEST(reserved_credits,COALESCE(actual_credits,0))),0),
                         COUNT(*),COALESCE(SUM(qualified_count),0),MAX(reserved_at)
                         FROM trial_requests WHERE reserved_at >= %s AND reserved_at < %s""",
                        (started, ends)).fetchone()
    used, count, qualified, last = usage
    state = "stopped" if stopped else "expired" if at >= ends else "budget_exhausted" if used >= limit else "active"
    return {"status": state, "started_at": started.isoformat(), "ends_at": ends.isoformat(),
            "credits_used_or_reserved": used, "credits_remaining": max(0, limit-used),
            "scan_count": count, "qualified_count": qualified,
            "last_scan_at": last.isoformat() if last else None}


def reserve(con, sport_key, at=None):
    """Atomic quota reservation; no automatic trial start."""
    import re
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", sport_key):
        raise ValueError("Invalid sport key")
    at = at or datetime.now(timezone.utc)
    with con.transaction():
        initialize(con)
        con.execute("SELECT pg_advisory_xact_lock(350024)")
        status = monitor(con, at)
        if status["status"] != "active" or status["credits_remaining"] < RESERVATION:
            return None
        recent = con.execute(
            "SELECT 1 FROM trial_requests WHERE sport_key=%s AND reserved_at>%s LIMIT 1",
            (sport_key, at-timedelta(minutes=5)),
        ).fetchone()
        if recent:
            return None
        return con.execute(
            "INSERT INTO trial_requests(sport_key,reserved_at,reserved_credits) "
            "VALUES (%s,%s,%s) RETURNING id", (sport_key,at,RESERVATION)
        ).fetchone()[0]


def reconcile(con, request_id, actual_credits, qualified_count=0, status="completed"):
    import math
    if actual_credits is not None and (not isinstance(actual_credits,(int,float)) or actual_credits < 0):
        raise ValueError("Invalid provider cost")
    if not isinstance(qualified_count,int) or qualified_count < 0:
        raise ValueError("Invalid qualified count")
    with con.transaction():
        con.execute(
            "UPDATE trial_requests SET actual_credits=%s,qualified_count=%s,status=%s WHERE id=%s",
            (math.ceil(actual_credits) if actual_credits is not None else None,
             qualified_count,status,request_id),
        )


def stop(con):
    with con.transaction():
        con.execute("UPDATE trial_control SET stopped=TRUE WHERE id=1")
