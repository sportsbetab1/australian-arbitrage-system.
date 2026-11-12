import json
from datetime import datetime, timedelta, timezone

import pytest

from app import database, push_delivery


@pytest.fixture
def delivery_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "delivery.db"))
    database.init_db()
    with database.get_db() as con:
        user_id = con.execute(
            "INSERT INTO auth_users(username, display_name, password_hash) "
            "VALUES ('test-user', 'Test User', 'unused')"
        ).lastrowid
        con.execute(
            "INSERT INTO push_subscriptions(user_id, subscription_json) VALUES (?, ?)",
            (
                user_id,
                json.dumps({
                    "endpoint": "https://fcm.googleapis.com/test",
                    "keys": {"p256dh": "test-key", "auth": "test-auth"},
                }),
            ),
        )
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "public-test")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "private-test")
    return user_id


def test_failed_push_is_queued_then_retried_and_marked_sent(delivery_db, monkeypatch):
    user_id = delivery_db
    calls = []

    class ProviderError(Exception):
        response = type("Response", (), {"status_code": 503})()

    def fail_once(subscription, payload, **kwargs):
        calls.append((subscription, payload, kwargs))
        if len(calls) == 1:
            raise ProviderError("do not persist provider details")

    monkeypatch.setattr(push_delivery, "webpush", fail_once)
    assert push_delivery.enqueue({"title": "Position settled"}, user_id) == 1

    first = push_delivery.deliver_due()
    assert first == {"sent": 0, "failed": 1, "queued": 1, "disabled": False}
    with database.get_db() as con:
        queued = con.execute(
            "SELECT status, attempts, next_attempt_at, last_error, payload_json "
            "FROM push_delivery_queue"
        ).fetchone()
        assert queued["status"] == "pending"
        assert queued["attempts"] == 1
        assert queued["next_attempt_at"] > datetime.now(timezone.utc).isoformat()
        assert queued["last_error"] == "push_provider_http_503"
        assert "provider details" not in queued["last_error"]

        con.execute(
            "UPDATE push_delivery_queue SET next_attempt_at = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
        )

    second = push_delivery.deliver_due()
    assert second == {"sent": 1, "failed": 0, "queued": 0, "disabled": False}
    assert len(calls) == 2
    assert calls[1][0]["endpoint"] == "https://fcm.googleapis.com/test"
    assert json.loads(calls[1][1])["title"] == "Position settled"
    with database.get_db() as con:
        sent = con.execute(
            "SELECT status, attempts, sent_at, locked_at FROM push_delivery_queue"
        ).fetchone()
        assert sent["status"] == "sent"
        assert sent["attempts"] == 2
        assert sent["sent_at"]
        assert sent["locked_at"] is None


def test_push_queue_waits_until_vapid_keys_are_configured(delivery_db, monkeypatch):
    monkeypatch.delenv("VAPID_PUBLIC_KEY")
    monkeypatch.delenv("VAPID_PRIVATE_KEY")
    push_delivery.enqueue({"title": "Queued"}, delivery_db)

    result = push_delivery.deliver_due()
    assert result["disabled"] is True
    assert result["queued"] == 1
    with database.get_db() as con:
        row = con.execute(
            "SELECT status, attempts FROM push_delivery_queue"
        ).fetchone()
    assert row["status"] == "pending"
    assert row["attempts"] == 0


def test_push_delivery_stops_after_max_attempts(delivery_db, monkeypatch):
    user_id = delivery_db
    push_delivery.enqueue({"title": "Give up"}, user_id)
    with database.get_db() as con:
        con.execute(
            "UPDATE push_delivery_queue SET attempts = ?, next_attempt_at = ?",
            (
                push_delivery.MAX_ATTEMPTS - 1,
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            ),
        )

    def always_fail(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(push_delivery, "webpush", always_fail)
    result = push_delivery.deliver_due()
    assert result["failed"] == 1
    assert result["queued"] == 0
    with database.get_db() as con:
        row = con.execute(
            "SELECT status, attempts, last_error FROM push_delivery_queue"
        ).fetchone()
    assert row["status"] == "dead"
    assert row["attempts"] == push_delivery.MAX_ATTEMPTS
    assert row["last_error"] == "RuntimeError"


def test_disabled_user_cannot_receive_new_push_events(delivery_db):
    with database.get_db() as con:
        con.execute("UPDATE auth_users SET is_active = 0 WHERE id = ?", (delivery_db,))
    assert push_delivery.enqueue({"title": "Shared event"}) == 0
    assert push_delivery.enqueue({"title": "Direct event"}, delivery_db) == 0


def test_queued_push_is_discarded_when_recipient_is_disabled(delivery_db, monkeypatch):
    assert push_delivery.enqueue({"title": "Private event"}, delivery_db) == 1
    with database.get_db() as con:
        con.execute("UPDATE auth_users SET is_active = 0 WHERE id = ?", (delivery_db,))
    sent = []
    monkeypatch.setattr(push_delivery, "webpush", lambda *args, **kwargs: sent.append(args))
    result = push_delivery.deliver_due()
    assert result["sent"] == 0
    assert result["queued"] == 0
    assert sent == []
    with database.get_db() as con:
        row = con.execute("SELECT status, attempts, last_error FROM push_delivery_queue").fetchone()
        assert tuple(row) == ("dead", 0, "recipient_inactive")

