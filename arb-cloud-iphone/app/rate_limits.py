"""Small fixed-window request budgets shared by workers through SQLite."""
from contextlib import closing
import hashlib
import math
import time

from app.database import get_db


def initialise():
    with closing(get_db()) as con, con:
        con.execute("""CREATE TABLE IF NOT EXISTS request_budgets (
            bucket TEXT PRIMARY KEY, used INTEGER NOT NULL, expires_at INTEGER NOT NULL
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_request_budgets_expiry ON request_budgets(expires_at)")


def consume(scope, identity, limit, window=60, timestamp=None):
    """Return Retry-After seconds, or zero when the request is allowed."""
    instant = time.time() if timestamp is None else timestamp
    start = math.floor(instant / window) * window
    expiry = start + window
    # Do not retain IP addresses, usernames, or tokens in the budget table.
    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
    bucket = f"{scope}:{start}:{digest}"
    with closing(get_db()) as con, con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM request_budgets WHERE expires_at <= ?", (instant,))
        row = con.execute("SELECT used FROM request_budgets WHERE bucket = ?", (bucket,)).fetchone()
        if row and row["used"] >= limit:
            return max(1, math.ceil(expiry - instant))
        con.execute("""INSERT INTO request_budgets(bucket, used, expires_at) VALUES (?, 1, ?)
            ON CONFLICT(bucket) DO UPDATE SET used = used + 1""", (bucket, expiry))
    return 0
