"""Durable push delivery queue with bounded exponential retry.

Run `python -m app.push_delivery` from the app directory on a scheduler to
retry deliveries that failed after their initial API request.
"""
from contextlib import closing
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

from pywebpush import webpush

from app.database import get_db


MAX_ATTEMPTS = 10
MAX_BATCH = 100


def _now():
    return datetime.now(timezone.utc)


def _now_text():
    return _now().isoformat()


def enqueue_with_connection(con, payload, user_id=None):
    serialized_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    if user_id is None:
        rows = con.execute(
            "SELECT s.user_id, s.subscription_json FROM push_subscriptions s "
            "JOIN auth_users u ON u.id = s.user_id WHERE u.is_active = 1"
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT s.user_id, s.subscription_json FROM push_subscriptions s "
            "JOIN auth_users u ON u.id = s.user_id WHERE u.is_active = 1 AND s.user_id = ?",
            (user_id,),
        ).fetchall()

    created = 0
    for row in rows:
        dedupe = hashlib.sha256(
            (str(row["user_id"]) + "\\0" + row["subscription_json"] + "\\0" + serialized_payload)
            .encode("utf-8")
        ).hexdigest()
        cursor = con.execute(
            """
            INSERT OR IGNORE INTO push_delivery_queue(
                user_id, subscription_json, payload_json, dedupe_key,
                status, attempts, next_attempt_at, created_at
            ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
            """,
            (
                row["user_id"],
                row["subscription_json"],
                serialized_payload,
                dedupe,
                _now_text(),
                _now_text(),
            ),
        )
        created += cursor.rowcount
    return created


def enqueue(payload, user_id=None):
    with closing(get_db()) as con, con:
        con.execute("BEGIN IMMEDIATE")
        return enqueue_with_connection(con, payload, user_id=user_id)

def _safe_error(error):
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        return f"push_provider_http_{status_code}"
    return type(error).__name__[:80]


def deliver_due(limit=MAX_BATCH):
    limit = max(1, min(int(limit), MAX_BATCH))
    public_key = os.getenv("VAPID_PUBLIC_KEY")
    private_key = os.getenv("VAPID_PRIVATE_KEY")
    if not public_key or not private_key:
        with closing(get_db()) as con, con:
            queued = con.execute(
                "SELECT COUNT(*) AS queued FROM push_delivery_queue "
                "WHERE status IN ('pending', 'sending')"
            ).fetchone()["queued"]
        return {"sent": 0, "failed": 0, "queued": queued, "disabled": True}

    subject = os.getenv("VAPID_SUBJECT", "mailto:admin@example.com")
    sent = failed = 0

    for _ in range(limit):
        with closing(get_db()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            now = _now()
            con.execute(
                """
                UPDATE push_delivery_queue
                SET status = 'dead', locked_at = NULL,
                    last_error = 'recipient_inactive', updated_at = ?
                WHERE status IN ('pending', 'sending') AND user_id IN (
                    SELECT id FROM auth_users WHERE is_active = 0
                )
                """,
                (now.isoformat(),),
            )
            stale_before = (now - timedelta(minutes=5)).isoformat()
            con.execute(
                """
                UPDATE push_delivery_queue
                SET status = 'pending', locked_at = NULL
                WHERE status = 'sending' AND locked_at < ?
                """,
                (stale_before,),
            )
            row = con.execute(
                """
                SELECT id, subscription_json, payload_json, attempts
                FROM push_delivery_queue
                WHERE status = 'pending' AND next_attempt_at <= ?
                ORDER BY next_attempt_at, id LIMIT 1
                """,
                (now.isoformat(),),
            ).fetchone()
            if row is None:
                break
            attempt = int(row["attempts"]) + 1
            con.execute(
                """
                UPDATE push_delivery_queue
                SET status = 'sending', attempts = ?, locked_at = ?, last_error = NULL
                WHERE id = ? AND status = 'pending'
                """,
                (attempt, now.isoformat(), row["id"]),
            )

        try:
            webpush(
                json.loads(row["subscription_json"]),
                row["payload_json"],
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
                timeout=10,
            )
        except Exception as error:
            failed += 1
            retryable = attempt < MAX_ATTEMPTS
            delay_seconds = min(30 * (2 ** min(attempt - 1, 10)), 6 * 60 * 60)
            next_attempt = (_now() + timedelta(seconds=delay_seconds)).isoformat()
            with closing(get_db()) as con, con:
                con.execute(
                    """
                    UPDATE push_delivery_queue
                    SET status = ?, next_attempt_at = ?, locked_at = NULL,
                        last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        "pending" if retryable else "dead",
                        next_attempt,
                        _safe_error(error),
                        _now_text(),
                        row["id"],
                    ),
                )
        else:
            sent += 1
            with closing(get_db()) as con, con:
                con.execute(
                    """
                    UPDATE push_delivery_queue
                    SET status = 'sent', sent_at = ?, locked_at = NULL,
                        last_error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (_now_text(), _now_text(), row["id"]),
                )

    with closing(get_db()) as con, con:
        counts = con.execute(
            """
            SELECT COUNT(*) AS queued
            FROM push_delivery_queue
            WHERE status IN ('pending', 'sending')
            """
        ).fetchone()
    return {"sent": sent, "failed": failed, "queued": counts["queued"], "disabled": False}


def delivery_status():
    with closing(get_db()) as con, con:
        rows = con.execute(
            "SELECT status, COUNT(*) AS total FROM push_delivery_queue GROUP BY status"
        ).fetchall()
        dead = con.execute(
            """
            SELECT id, attempts, last_error, created_at, updated_at
            FROM push_delivery_queue
            WHERE status = 'dead'
            ORDER BY updated_at DESC, id DESC
            LIMIT 20
            """
        ).fetchall()
    counts = {status: 0 for status in ("pending", "sending", "sent", "dead")}
    counts.update({row["status"]: row["total"] for row in rows})
    return {
        "counts": counts,
        "dead_letters": [dict(row) for row in dead],
    }


def main():
    parser = argparse.ArgumentParser(description="Retry due push notification deliveries.")
    parser.add_argument("--limit", type=int, default=MAX_BATCH)
    args = parser.parse_args()
    result = deliver_due(args.limit)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

