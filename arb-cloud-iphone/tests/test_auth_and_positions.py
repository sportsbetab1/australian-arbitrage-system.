import json

import pytest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import database, main


SETUP_TOKEN = "test-only-setup-token-long-enough"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test-arbitrage.db"))
    monkeypatch.setattr(main, "AUTH_COOKIE_SECURE", False)
    monkeypatch.setenv("AUTH_SETUP_TOKEN", SETUP_TOKEN)

    with TestClient(main.app, base_url="http://testserver") as test_client:
        response = test_client.post(
            "/api/auth/setup",
            headers={"Origin": "http://testserver"},
            json={
                "setup_token": SETUP_TOKEN,
                "hunta_password": "Hunta-test-passphrase-2026",
                "jay_password": "Jay-test-passphrase-2026",
            },
        )
        assert response.status_code == 200
        yield test_client


def login(client, username="hunta", password="Hunta-test-passphrase-2026"):
    return client.post(
        "/api/auth/login",
        headers={"Origin": "http://testserver"},
        json={"username": username, "password": password},
    )


def test_authentication_protects_api_and_login_creates_session(client):
    assert client.get("/api/accounts").status_code == 401
    assert client.get("/api/push/delivery-status").status_code == 401

    response = login(client)
    assert response.status_code == 200
    assert response.cookies.get(main.AUTH_COOKIE_NAME)
    assert client.get("/api/accounts").status_code == 200


def test_mutating_api_rejects_missing_or_cross_origin_origin(client):
    missing = client.post(
        "/api/auth/login",
        json={"username": "hunta", "password": "wrong-password"},
    )
    assert missing.status_code == 403

    cross_origin = client.post(
        "/api/auth/login",
        headers={"Origin": "https://attacker.example"},
        json={"username": "hunta", "password": "wrong-password"},
    )
    assert cross_origin.status_code == 403

    malformed_origin = client.post(
        "/api/auth/login",
        headers={"Origin": "http://[broken"},
        json={"username": "hunta", "password": "wrong-password"},
    )
    assert malformed_origin.status_code == 403


def test_logout_revokes_copied_session_token(client):
    response = login(client)
    token = response.cookies[main.AUTH_COOKIE_NAME]
    assert client.post("/api/auth/logout", headers={"Origin": "http://testserver"}).status_code == 200
    client.cookies.set(main.AUTH_COOKIE_NAME, token)
    assert client.get("/api/accounts").status_code == 401
    assert client.get("/api/auth/status").json()["authenticated"] is False


def test_concurrent_failed_logins_cannot_lose_lockout_counts(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace
    from fastapi import HTTPException, Response

    barrier = Barrier(5)
    real_hasher = main.password_hasher
    def fail_verification(*args):
        barrier.wait(timeout=15)
        raise main.VerifyMismatchError("Test mismatch")
    monkeypatch.setattr(main, "password_hasher", SimpleNamespace(verify=fail_verification))
    def attempt(_):
        try:
            main.login(main.AuthLogin(username="hunta", password="incorrect-test-password"), Response())
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert list(pool.map(attempt, range(5))) == [401] * 5
    monkeypatch.setattr(main, "password_hasher", real_hasher)
    assert login(client).status_code == 423
    with database.get_db() as con:
        row = con.execute("SELECT failed_attempts, locked_until FROM auth_users WHERE username = 'hunta'").fetchone()
        assert row["failed_attempts"] == 5
        assert row["locked_until"]
        assert con.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["deactivate", "rotate_password"])
def test_account_change_during_password_verification_prevents_session(client, monkeypatch, change):
    from types import SimpleNamespace

    real_hasher = main.password_hasher
    def change_during_verification(stored_hash, password):
        real_hasher.verify(stored_hash, password)
        with database.get_db() as con:
            if change == "deactivate":
                con.execute("UPDATE auth_users SET is_active = 0 WHERE username = 'hunta'")
            else:
                con.execute("UPDATE auth_users SET password_hash = ? WHERE username = 'hunta'",
                            (real_hasher.hash("New-admin-rotated-passphrase"),))
        return True
    monkeypatch.setattr(main, "password_hasher", SimpleNamespace(verify=change_during_verification))
    assert login(client).status_code == 401
    with database.get_db() as con:
        assert con.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0


def test_deactivation_revokes_access_from_existing_session(client):
    assert login(client).status_code == 200
    with database.get_db() as con:
        con.execute("UPDATE auth_users SET is_active = 0 WHERE username = 'hunta'")
    assert client.get("/api/accounts").status_code == 401
    assert client.get("/api/auth/status").json()["authenticated"] is False


def test_api_responses_and_auth_errors_are_not_cacheable(client):
    responses = [client.get("/api/accounts"), client.get("/api/auth/status"),
                 client.post("/api/auth/login", json={"username": "hunta", "password": "bad"}),
                 login(client, password="incorrect-test-password")]
    assert [r.status_code for r in responses] == [401, 200, 403, 401]
    assert login(client).status_code == 200
    responses.extend([client.get("/api/accounts"), client.get("/api/reports/settlements.csv")])
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"


def test_non_ascii_setup_token_is_rejected_without_server_error(client):
    response = client.post("/api/auth/setup", headers={"Origin": "http://testserver"}, json={
        "setup_token": "incorrect-token-🔒", "hunta_password": "Hunta-test-passphrase-2026",
        "jay_password": "Jay-test-passphrase-2026"})
    assert response.status_code == 403


@pytest.fixture()
def cashout_position(client, monkeypatch):
    monkeypatch.setattr(main, "send_push", lambda *args, **kwargs: 0)
    assert login(client).status_code == 200
    headers = {"Origin": "http://testserver"}
    accounts = client.get("/api/accounts").json()
    chosen = [next(a for a in accounts if a["owner"] == owner and a["bookmaker"] == book)
              for owner, book in [("Hunta", "Sportsbet"), ("Jay", "Betfair")]]
    for account in chosen:
        assert client.post(f"/api/accounts/{account['id']}/opening-balance", headers=headers,
                           json={"amount": 500}).status_code == 200

    def open_position(unmatched=False):
        payload = {
            "sport": "Test", "event": "Cashout fixture", "market": "Winner",
            "event_id": "cashout-event", "market_id": "cashout-market", "market_type": "binary",
            "possible_outcomes": ["home", "away"], "market_rules_verified": True,
            "manual_confirmation": True,
            "legs": [{"account_id": a["id"], "event_id": "cashout-event", "market_id": "cashout-market",
                      "outcome_id": outcome, "selection": outcome, "odds": 2.2, "stake": 100,
                      "manually_accepted": not (unmatched and index == 1)}
                     for index, (a, outcome) in enumerate(zip(chosen, ["home", "away"]))],
        }
        response = client.post("/api/positions/" + ("unmatched" if unmatched else "open"),
                               headers=headers, json=payload)
        assert response.status_code == 200, response.text
        position_id = response.json()["position_id"]
        return next(p for p in client.get("/api/positions").json() if p["id"] == position_id)

    return open_position, headers


def test_cashout_net_credit_mixed_settlement_and_duplicate_protection(client, cashout_position):
    open_position, headers = cashout_position
    position = open_position()
    home, away = position["legs"]
    endpoint = f"/api/positions/{position['id']}/legs/{away['id']}/cashout"
    payload = {"actual_return": 120, "notes": "Bookmaker reference C123", "manual_confirmation": True}
    response = client.post(endpoint, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["commission_paid"] == 0
    assert response.json()["remaining_legs"] == 1
    assert client.get("/api/dashboard").json()["open_exposure"] == 200
    assert next(a for a in client.get("/api/accounts").json() if a["id"] == away["account_id"])["balance"] == 520
    assert client.post(endpoint, headers=headers, json=payload).status_code == 409
    assert client.post(endpoint.replace("/cashout", "/settle"), headers=headers,
                       json={"actual_return": 200}).status_code == 409

    final = client.post(f"/api/positions/{position['id']}/legs/{home['id']}/settle",
                        headers=headers, json={"actual_return": 0})
    assert final.status_code == 200, final.text
    assert final.json()["actual_pnl"] == -80
    assert client.get("/api/dashboard").json()["open_exposure"] == 0
    assert client.get("/api/reports/summary").json()["positions"]["net_realised_pnl"] == -80
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(client.get("/api/reports/settlements.csv").text)))
    assert rows[0]["cashout_legs"] == "1"
    with database.get_db() as con:
        entries = con.execute("SELECT amount FROM account_transactions WHERE transaction_type = 'cashout_return'").fetchall()
        assert [e["amount"] for e in entries] == [120]
        details = json.loads(con.execute("SELECT details FROM audit_log WHERE action = 'position_leg_cashed_out'").fetchone()["details"])
        assert details["recorded_by"] == "hunta"
        assert details["notes"] == "Bookmaker reference C123"
    notifications = client.get("/api/notifications").json()["items"]
    assert any(n["event_type"] == "position_leg_cashed_out" for n in notifications)


@pytest.mark.parametrize("changes,status", [
    ({"actual_return": -1}, 422), ({"actual_return": "Infinity"}, 422),
    ({"actual_return": 1.001}, 422), ({"notes": "   "}, 422),
    ({"manual_confirmation": False}, 400), ({"manual_confirmation": "true"}, 422),
])
def test_invalid_cashouts_do_not_change_balances(client, cashout_position, changes, status):
    open_position, headers = cashout_position
    position = open_position()
    leg = position["legs"][0]
    payload = {"actual_return": 90, "notes": "Confirmed full cashout", "manual_confirmation": True, **changes}
    response = client.post(f"/api/positions/{position['id']}/legs/{leg['id']}/cashout",
                           headers=headers, json=payload)
    assert response.status_code == status, response.text
    assert client.get("/api/dashboard").json()["open_exposure"] == 200
    with database.get_db() as con:
        assert con.execute("SELECT COUNT(*) FROM account_transactions WHERE transaction_type = 'cashout_return'").fetchone()[0] == 0


def test_unmatched_cashout_closes_only_accepted_leg(client, cashout_position):
    open_position, headers = cashout_position
    position = open_position(unmatched=True)
    accepted = next(l for l in position["legs"] if l["accepted"])
    rejected = next(l for l in position["legs"] if not l["accepted"])
    payload = {"actual_return": 0, "notes": "Full close with zero credit", "manual_confirmation": True}
    endpoint = f"/api/positions/{position['id']}/legs/"
    assert client.post(endpoint + str(rejected["id"]) + "/cashout", headers=headers, json=payload).status_code == 409
    response = client.post(endpoint + str(accepted["id"]) + "/cashout", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["position_status"] == "Settled"
    assert response.json()["actual_pnl"] == -100
    assert client.get("/api/dashboard").json()["open_exposure"] == 0


def test_concurrent_cashout_and_settlement_credit_leg_once(client, cashout_position):
    from concurrent.futures import ThreadPoolExecutor

    open_position, headers = cashout_position
    position = open_position()
    leg = position["legs"][0]
    endpoint = f"/api/positions/{position['id']}/legs/{leg['id']}/"
    def close(kind):
        payload = {"actual_return": 110, "notes": "Concurrent close"}
        if kind == "cashout":
            payload["manual_confirmation"] = True
        return client.post(endpoint + kind, headers=headers, json=payload).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(close, ["cashout", "settle"]))
    assert sorted(statuses) == [200, 409]
    with database.get_db() as con:
        assert con.execute("SELECT COUNT(*) FROM account_transactions WHERE reference_id = ? AND transaction_type IN ('cashout_return', 'settlement_return')", (leg["id"],)).fetchone()[0] == 1


def test_full_cashouts_close_profitable_position_and_allocate_once(client, cashout_position):
    open_position, headers = cashout_position
    position = open_position()
    for leg, amount in zip(position["legs"], [110, 120]):
        result = client.post(f"/api/positions/{position['id']}/legs/{leg['id']}/cashout", headers=headers,
                             json={"actual_return": amount, "notes": "Full cashout credited", "manual_confirmation": True})
        assert result.status_code == 200, result.text
    assert result.json()["position_status"] == "Settled"
    assert result.json()["actual_pnl"] == 30
    assert result.json()["total_commission"] == 0
    with database.get_db() as con:
        assert con.execute("SELECT COUNT(*) FROM profit_allocations WHERE position_id = ?", (position["id"],)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM position_legs WHERE position_id = ? AND close_type = 'cashout'", (position["id"],)).fetchone()[0] == 2


def test_cashout_migration_preserves_legacy_settlements(client, cashout_position):
    open_position, headers = cashout_position
    position = open_position()
    home, away = position["legs"]
    assert client.post(f"/api/positions/{position['id']}/legs/{home['id']}/settle", headers=headers,
                       json={"actual_return": 210}).status_code == 200
    with database.get_db() as con:
        con.execute("ALTER TABLE position_legs DROP COLUMN close_type")
    database.migrate_position_schema()
    database.migrate_position_schema()
    with database.get_db() as con:
        legacy = con.execute("SELECT settled, actual_return, close_type FROM position_legs WHERE id = ?", (home["id"],)).fetchone()
        assert tuple(legacy) == (1, 210, None)
    final = client.post(f"/api/positions/{position['id']}/legs/{away['id']}/cashout", headers=headers,
                        json={"actual_return": 120, "notes": "Post-migration cashout", "manual_confirmation": True})
    assert final.status_code == 200, final.text
    assert final.json()["actual_pnl"] == 130


def test_expired_login_lockout_resets_failed_attempts(client):
    con = database.get_db()
    try:
        con.execute(
            "UPDATE auth_users SET failed_attempts = 5, locked_until = ? WHERE username = ?",
            ("2000-01-01T00:00:00+00:00", "hunta"),
        )
        con.commit()
    finally:
        con.close()

    response = login(client, password="incorrect-test-password")
    assert response.status_code == 401

    con = database.get_db()
    try:
        row = con.execute(
            "SELECT failed_attempts, locked_until FROM auth_users WHERE username = ?",
            ("hunta",),
        ).fetchone()
        assert row["failed_attempts"] == 1
        assert row["locked_until"] is None
    finally:
        con.close()


def test_one_signed_in_person_can_record_both_confirmed_legs(client, monkeypatch):
    push_events = []
    monkeypatch.setattr(main, "send_push", lambda payload, user_id=None: push_events.append(payload) or 1)
    response = login(client)
    assert response.status_code == 200

    con = database.get_db()
    try:
        accounts = con.execute(
            "SELECT id, owner, bookmaker FROM accounts "
            "WHERE (owner = 'Hunta' AND bookmaker = 'Sportsbet') "
            "OR (owner = 'Jay' AND bookmaker = 'Betfair')"
        ).fetchall()
    finally:
        con.close()
    account_ids = {(row["owner"], row["bookmaker"]): row["id"] for row in accounts}
    with database.get_db() as con:
        users = con.execute("SELECT id, username FROM auth_users").fetchall()
        for user in users:
            con.execute(
                "INSERT INTO push_subscriptions(user_id, subscription_json) VALUES (?, ?)",
                (
                    user["id"],
                    json.dumps({
                        "endpoint": f"https://fcm.googleapis.com/{user['username']}",
                        "keys": {"p256dh": "test-key", "auth": "test-auth"},
                    }),
                ),
            )
    for account_id in account_ids.values():
        funded = client.post(
            f"/api/accounts/{account_id}/opening-balance",
            headers={"Origin": "http://testserver"},
            json={"amount": 500},
        )
        assert funded.status_code == 200, funded.text

    payload = {
        "sport": "Soccer",
        "event": "Isolated test fixture",
        "market": "Two-outcome test market",
        "event_id": "test-event",
        "market_id": "test-market",
        "market_type": "binary",
        "possible_outcomes": ["home", "away"],
        "market_rules_verified": True,
        "manual_confirmation": True,
        "notes": "Automated isolated test; no bookmaker action.",
        "legs": [
            {
                "account_id": account_ids[("Hunta", "Sportsbet")],
                "event_id": "test-event",
                "market_id": "test-market",
                "outcome_id": "home",
                "selection": "Home",
                "odds": 2.1,
                "stake": 100,
                "manually_accepted": True,
            },
            {
                "account_id": account_ids[("Jay", "Betfair")],
                "event_id": "test-event",
                "market_id": "test-market",
                "outcome_id": "away",
                "selection": "Away",
                "odds": 2.1,
                "stake": 100,
                "manually_accepted": True,
            },
        ],
    }

    opened = client.post(
        "/api/positions/open",
        headers={"Origin": "http://testserver"},
        json=payload,
    )
    assert opened.status_code == 200, opened.text
    result = opened.json()
    assert result["status"] == "Open"
    assert result["confirmed_by"] == "hunta"
    with database.get_db() as con:
        queued_events = con.execute(
            "SELECT COUNT(*) AS total FROM push_delivery_queue "
            "WHERE status = 'pending' AND payload_json LIKE '%position_opened%'"
        ).fetchone()["total"]
    assert queued_events == 2
    assert push_events[0]["event_type"] == "position_opened"
    assert "No bookmaker bets were placed" in result["message"]
    hunta_notifications = client.get("/api/notifications").json()
    assert hunta_notifications["unread_count"] == 1
    opened_alert = hunta_notifications["items"][0]
    assert opened_alert["event_type"] == "position_opened"
    assert opened_alert["position_id"] == result["position_id"]
    marked = client.post(
        f"/api/notifications/{opened_alert['id']}/read",
        headers={"Origin": "http://testserver"},
        json={},
    )
    assert marked.status_code == 200
    assert client.get("/api/notifications").json()["unread_count"] == 0

    balances = {
        (row["owner"], row["bookmaker"]): row["balance"]
        for row in client.get("/api/accounts").json()
    }
    assert balances[("Hunta", "Sportsbet")] == 400
    assert balances[("Jay", "Betfair")] == 400

    settled_first = client.post(
        f"/api/positions/{result['position_id']}/legs/1/settle",
        headers={"Origin": "http://testserver"},
        json={"actual_return": 210, "notes": "Test settlement"},
    )
    assert settled_first.status_code == 200
    settled_second = client.post(
        f"/api/positions/{result['position_id']}/legs/2/settle",
        headers={"Origin": "http://testserver"},
        json={"actual_return": 210, "notes": "Test settlement"},
    )
    assert settled_second.status_code == 200
    assert [event["event_type"] for event in push_events] == [
        "position_opened", "position_settled"
    ]
    hunta_notifications = client.get("/api/notifications").json()
    settled_alerts = [
        item for item in hunta_notifications["items"]
        if item["event_type"] == "position_settled"
    ]
    assert len(settled_alerts) == 1
    assert "Test settlement" not in settled_alerts[0]["message"]

    jay_login = login(client, username="jay", password="Jay-test-passphrase-2026")
    assert jay_login.status_code == 200
    jay_notifications = client.get("/api/notifications").json()
    assert {item["event_type"] for item in jay_notifications["items"]} == {
        "position_opened", "position_leg_settled", "position_settled"
    }
    assert client.post(
        f"/api/notifications/{opened_alert['id']}/read",
        headers={"Origin": "http://testserver"},
        json={},
    ).status_code == 404

    balances = {
        (row["owner"], row["bookmaker"]): row["balance"]
        for row in client.get("/api/accounts").json()
    }
    assert balances[("Hunta", "Sportsbet")] == 610
    assert balances[("Jay", "Betfair")] == 603.4

    below_floor_payload = {
        **payload,
        "event": "Accepted prices below candidate floor",
        "event_id": "below-floor-event",
        "market_id": "below-floor-market",
        "legs": [
            {
                **payload["legs"][0],
                "event_id": "below-floor-event",
                "market_id": "below-floor-market",
                "odds": 1.5,
                "stake": 50,
            },
            {
                **payload["legs"][1],
                "event_id": "below-floor-event",
                "market_id": "below-floor-market",
                "odds": 1.5,
                "stake": 50,
            },
        ],
    }
    recorded_below_floor = client.post(
        "/api/positions/open",
        headers={"Origin": "http://testserver"},
        json=below_floor_payload,
    )
    assert recorded_below_floor.status_code == 200, recorded_below_floor.text
    assert recorded_below_floor.json()["roi"] < main.MIN_NET_ROI
    assert recorded_below_floor.json()["total_stake"] == 100
    assert "below the 1.5% candidate threshold" in recorded_below_floor.json()["warning"]
    balances = {
        (row["owner"], row["bookmaker"]): row["balance"]
        for row in client.get("/api/accounts").json()
    }
    assert balances[("Hunta", "Sportsbet")] == 560
    assert balances[("Jay", "Betfair")] == 553.4


def test_unmatched_leg_is_reserved_pauses_new_positions_and_can_settle(client):
    assert login(client).status_code == 200

    con = database.get_db()
    try:
        accounts = con.execute(
            "SELECT id, owner, bookmaker FROM accounts "
            "WHERE (owner = 'Hunta' AND bookmaker = 'Sportsbet') "
            "OR (owner = 'Jay' AND bookmaker = 'Betfair')"
        ).fetchall()
    finally:
        con.close()
    account_ids = {(row["owner"], row["bookmaker"]): row["id"] for row in accounts}
    for account_id in account_ids.values():
        funded = client.post(
            f"/api/accounts/{account_id}/opening-balance",
            headers={"Origin": "http://testserver"},
            json={"amount": 500},
        )
        assert funded.status_code == 200, funded.text

    payload = {
        "sport": "Soccer",
        "event": "Isolated unmatched test",
        "market": "Two-outcome test market",
        "event_id": "unmatched-event",
        "market_id": "unmatched-market",
        "market_type": "binary",
        "possible_outcomes": ["home", "away"],
        "market_rules_verified": True,
        "manual_confirmation": True,
        "notes": "Isolated test; first bookmaker accepted, second rejected.",
        "legs": [
            {
                "account_id": account_ids[("Hunta", "Sportsbet")],
                "event_id": "unmatched-event",
                "market_id": "unmatched-market",
                "outcome_id": "home",
                "selection": "Home",
                "odds": 2.1,
                "stake": 100,
                "manually_accepted": True,
            },
            {
                "account_id": account_ids[("Jay", "Betfair")],
                "event_id": "unmatched-event",
                "market_id": "unmatched-market",
                "outcome_id": "away",
                "selection": "Away",
                "odds": 2.1,
                "stake": 100,
                "manually_accepted": False,
            },
        ],
    }
    headers = {"Origin": "http://testserver"}

    recorded = client.post("/api/positions/unmatched", headers=headers, json=payload)
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["status"] == "Unmatched"
    assert client.get("/api/dashboard").json()["open_positions"] == 1

    normal_open = {**payload, "event": "Blocked while unmatched"}
    normal_open["legs"] = [
        {**leg, "manually_accepted": True} for leg in payload["legs"]
    ]
    paused = client.post("/api/positions/open", headers=headers, json=normal_open)
    assert paused.status_code == 409
    assert "unmatched position" in paused.json()["detail"]

    position = next(
        item for item in client.get("/api/positions").json()
        if item["id"] == recorded.json()["position_id"]
    )
    accepted_leg = next(leg for leg in position["legs"] if leg["accepted"])
    settled = client.post(
        "/api/positions/{}/legs/{}/settle".format(
            position["id"], accepted_leg["id"]
        ),
        headers=headers,
        json={"actual_return": 210, "notes": "Test settlement"},
    )
    assert settled.status_code == 200, settled.text
    assert settled.json()["position_status"] == "Settled"
    assert settled.json()["actual_pnl"] == 110
    assert client.get("/api/dashboard").json()["open_positions"] == 0

    duplicate = client.post(
        "/api/positions/{}/legs/{}/settle".format(
            position["id"], accepted_leg["id"]
        ),
        headers=headers,
        json={"actual_return": 210, "notes": "Duplicate submission"},
    )
    assert duplicate.status_code == 409

    con = database.get_db()
    try:
        settlement_audits = con.execute(
            "SELECT details FROM audit_log WHERE action = 'position_leg_settled'"
        ).fetchall()
        settlement_returns = con.execute(
            "SELECT COUNT(*) AS total FROM account_transactions "
            "WHERE transaction_type = 'settlement_return' AND reference_id = ?",
            (accepted_leg["id"],),
        ).fetchone()["total"]
    finally:
        con.close()
    assert len(settlement_audits) == 1
    assert settlement_returns == 1
    assert '"recorded_by": "hunta"' in settlement_audits[0]["details"]

def test_account_transactions_reject_withdrawals_beyond_recorded_balance(client):
    assert login(client).status_code == 200
    con = database.get_db()
    try:
        account_id = con.execute(
            "SELECT id FROM accounts WHERE owner = 'Hunta' AND bookmaker = 'Sportsbet'"
        ).fetchone()["id"]
    finally:
        con.close()

    headers = {"Origin": "http://testserver"}
    initialized = client.post(
        f"/api/accounts/{account_id}/opening-balance",
        headers=headers,
        json={"amount": 100},
    )
    assert initialized.status_code == 200

    deposited = client.post(
        f"/api/accounts/{account_id}/transactions",
        headers=headers,
        json={"transaction_type": "deposit", "amount": 50, "description": "Test deposit"},
    )
    assert deposited.status_code == 200
    assert deposited.json()["balance"] == 150

    withdrawn = client.post(
        f"/api/accounts/{account_id}/transactions",
        headers=headers,
        json={"transaction_type": "withdrawal", "amount": 20, "description": "Test withdrawal"},
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["balance"] == 130

    overdrawn = client.post(
        f"/api/accounts/{account_id}/transactions",
        headers=headers,
        json={"transaction_type": "withdrawal", "amount": 131, "description": "Overdraw test"},
    )
    assert overdrawn.status_code == 409


def test_live_odds_scan_is_manual_and_returns_qualified_candidate(client, monkeypatch):
    assert login(client).status_code == 200
    updated = datetime.now(timezone.utc).isoformat()
    event = {
        "id": "provider-event-1",
        "sport_key": "soccer_epl",
        "sport_title": "Soccer",
        "home_team": "Test Home",
        "away_team": "Test Away",
        "commence_time": "2026-11-20T10:00:00Z",
        "bookmakers": [{
            "key": "sportsbet",
            "title": "Sportsbet",
            "markets": [{
                "key": "h2h",
                "last_update": updated,
                "outcomes": [
                    {"name": "Test Home", "price": 4.5},
                    {"name": "Draw", "price": 4.5},
                    {"name": "Test Away", "price": 4.5},
                ],
            }],
        }],
    }
    monkeypatch.setattr(
        main,
        "fetch_json",
        lambda *args, **kwargs: ([event], {"requests_remaining": 99}),
    )

    response = client.get("/api/odds/scan/soccer_epl")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["qualified_count"] == 1
    assert result["candidates"][0]["candidate"]["event"] == "Test Home vs Test Away"
    assert result["quota"]["requests_remaining"] == 99
    assert "manually" in result["candidates"][0]["warning"]


def test_logout_revokes_session_and_removes_cookie(client):
    response = login(client)
    assert response.status_code == 200
    assert client.get("/api/accounts").status_code == 200

    logged_out = client.post(
        "/api/auth/logout",
        headers={"Origin": "http://testserver"},
        json={},
    )
    assert logged_out.status_code == 200
    assert client.get("/api/accounts").status_code == 401


def test_expired_session_is_rejected_and_revoked(client):
    response = login(client)
    assert response.status_code == 200
    token = response.cookies.get(main.AUTH_COOKIE_NAME)
    assert token

    con = database.get_db()
    try:
        con.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE token_hash = ?",
            ("2000-01-01T00:00:00+00:00", main.token_digest(token)),
        )
        con.commit()
    finally:
        con.close()

    assert client.get("/api/accounts").status_code == 401
    con = database.get_db()
    try:
        remaining = con.execute(
            "SELECT COUNT(*) AS total FROM auth_sessions WHERE token_hash = ?",
            (main.token_digest(token),),
        ).fetchone()["total"]
        assert remaining == 0
    finally:
        con.close()


def test_setup_cannot_replace_existing_accounts(client):
    response = client.post(
        "/api/auth/setup",
        headers={"Origin": "http://testserver"},
        json={
            "setup_token": SETUP_TOKEN,
            "hunta_password": "Hunta-another-passphrase-2026",
            "jay_password": "Jay-another-passphrase-2026",
        },
    )
    assert response.status_code == 409


def test_unmatched_position_accepts_only_risk_improving_manual_hedge(client):
    assert login(client).status_code == 200
    con = database.get_db()
    try:
        rows = con.execute(
            """
            SELECT id, owner, bookmaker FROM accounts
            WHERE (owner = 'Hunta' AND bookmaker = 'Sportsbet')
               OR (owner = 'Jay' AND bookmaker = 'Betfair')
               OR (owner = 'Jay' AND bookmaker = 'Unibet')
            """
        ).fetchall()
    finally:
        con.close()
    account_ids = {(row["owner"], row["bookmaker"]): row["id"] for row in rows}
    headers = {"Origin": "http://testserver"}
    for account_id in account_ids.values():
        response = client.post(
            f"/api/accounts/{account_id}/opening-balance",
            headers=headers,
            json={"amount": 500},
        )
        assert response.status_code == 200

    payload = {
        "sport": "Soccer",
        "event": "Unmatched hedge scenario",
        "market": "Two-outcome test market",
        "event_id": "hedge-event",
        "market_id": "hedge-market",
        "market_type": "binary",
        "possible_outcomes": ["home", "away"],
        "market_rules_verified": True,
        "manual_confirmation": True,
        "legs": [
            {
                "account_id": account_ids[("Hunta", "Sportsbet")],
                "event_id": "hedge-event",
                "market_id": "hedge-market",
                "outcome_id": "home",
                "selection": "Home",
                "odds": 2.1,
                "stake": 100,
                "manually_accepted": True,
            },
            {
                "account_id": account_ids[("Jay", "Betfair")],
                "event_id": "hedge-event",
                "market_id": "hedge-market",
                "outcome_id": "away",
                "selection": "Away",
                "odds": 2.1,
                "stake": 100,
                "manually_accepted": False,
            },
        ],
    }
    opened = client.post("/api/positions/unmatched", headers=headers, json=payload)
    assert opened.status_code == 200, opened.text
    position_id = opened.json()["position_id"]

    hedge_payload = {
        "account_id": account_ids[("Jay", "Unibet")],
        "outcome_id": "home",
        "selection": "Home",
        "odds": 2.0,
        "stake": 20,
        "manual_confirmation": True,
        "manually_accepted": True,
    }
    rejected = client.post(
        f"/api/positions/{position_id}/hedge",
        headers=headers,
        json=hedge_payload,
    )
    assert rejected.status_code == 400
    assert "does not improve" in rejected.json()["detail"]

    hedge_payload.update({"outcome_id": "away", "selection": "Away", "stake": 55})
    recorded = client.post(
        f"/api/positions/{position_id}/hedge",
        headers=headers,
        json=hedge_payload,
    )
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["position_status"] == "Unmatched"
    assert recorded.json()["worst_case_profit_before"] == -100
    assert recorded.json()["worst_case_profit_after"] == -45
    assert client.get("/api/dashboard").json()["open_exposure"] == 255
    balances = {
        (row["owner"], row["bookmaker"]): row["balance"]
        for row in client.get("/api/accounts").json()
    }
    assert balances[("Jay", "Unibet")] == 445

    hedge_payload["stake"] = 10
    second_hedge = client.post(
        f"/api/positions/{position_id}/hedge",
        headers=headers,
        json=hedge_payload,
    )
    assert second_hedge.status_code == 200, second_hedge.text
    assert second_hedge.json()["worst_case_profit_before"] == -45
    assert second_hedge.json()["worst_case_profit_after"] == -35
    assert client.get("/api/dashboard").json()["open_exposure"] == 265

    non_improving = {
        **hedge_payload,
        "outcome_id": "home",
        "selection": "Home",
        "stake": 20,
    }
    rejected_again = client.post(
        f"/api/positions/{position_id}/hedge",
        headers=headers,
        json=non_improving,
    )
    assert rejected_again.status_code == 400

    position = next(
        item for item in client.get("/api/positions").json()
        if item["id"] == position_id
    )
    accepted_legs = [leg for leg in position["legs"] if leg["accepted"]]
    for index, leg in enumerate(accepted_legs):
        actual_return = 210 if leg["selection"] == "Home" else 0
        settled = client.post(
            f"/api/positions/{position_id}/legs/{leg['id']}/settle",
            headers=headers,
            json={"actual_return": actual_return, "notes": "Isolated hedge test"},
        )
        assert settled.status_code == 200, settled.text
        if index == 0:
            assert settled.json()["position_status"] == "Unmatched"
            assert settled.json()["remaining_legs"] == len(accepted_legs) - 1
            notifications = client.get("/api/notifications").json()["items"]
            assert any(
                item["event_type"] == "position_leg_settled"
                and item["position_id"] == position_id
                for item in notifications
            )
    final = client.get("/api/positions").json()
    settled_position = next(item for item in final if item["id"] == position_id)
    assert settled_position["status"] == "Settled"
    assert settled_position["actual_pnl"] == 45


def test_five_failed_logins_lock_account_until_timeout(client):
    for attempt in range(5):
        response = login(client, password="incorrect-test-password")
        assert response.status_code == 401, f"attempt {attempt + 1}: {response.text}"

    locked = login(client)
    assert locked.status_code == 423


def test_session_cookie_is_http_only_same_site_and_time_limited(client):
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "max-age=43200" in cookie


def test_push_subscriptions_are_user_scoped(client, monkeypatch):
    from app import push_delivery

    assert login(client, username="hunta").status_code == 200
    headers = {"Origin": "http://testserver"}
    subscription_hunta = {
        "endpoint": "https://fcm.googleapis.com/hunta",
        "keys": {"p256dh": "hunta-key", "auth": "hunta-auth"},
    }
    saved = client.post(
        "/api/push/subscribe",
        headers=headers,
        json={"subscription": subscription_hunta},
    )
    assert saved.status_code == 200

    con = database.get_db()
    try:
        hunta_id = con.execute(
            "SELECT id FROM auth_users WHERE username = 'hunta'"
        ).fetchone()["id"]
        jay_id = con.execute(
            "SELECT id FROM auth_users WHERE username = 'jay'"
        ).fetchone()["id"]
        row = con.execute(
            "SELECT user_id FROM push_subscriptions WHERE subscription_json LIKE '%hunta%'"
        ).fetchone()
        assert row["user_id"] == hunta_id
    finally:
        con.close()

    assert login(client, username="jay", password="Jay-test-passphrase-2026").status_code == 200
    subscription_jay = {
        "endpoint": "https://fcm.googleapis.com/jay",
        "keys": {"p256dh": "jay-key", "auth": "jay-auth"},
    }
    assert client.post(
        "/api/push/subscribe",
        headers=headers,
        json={"subscription": subscription_jay},
    ).status_code == 200

    delivered = []
    monkeypatch.setattr(
        push_delivery,
        "webpush",
        lambda subscription, payload, **kwargs: delivered.append(subscription["endpoint"]),
    )
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "public-test")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "private-test")

    assert main.send_push({"title": "test"}, user_id=hunta_id) == 1
    assert delivered == ["https://fcm.googleapis.com/hunta"]
    assert main.send_push({"title": "test"}, user_id=jay_id) == 1
    assert delivered[-1] == "https://fcm.googleapis.com/jay"
    queue_status = client.get("/api/push/delivery-status")
    assert queue_status.status_code == 200
    assert queue_status.json()["counts"]["sent"] == 2
    assert queue_status.json()["counts"]["dead"] == 0
    assert queue_status.json()["dead_letters"] == []


def test_push_subscription_requires_safe_https_endpoint(client):
    assert login(client).status_code == 200
    headers = {"Origin": "http://testserver"}
    for endpoint in (
        "http://push.example.test/sub",
        "https://localhost/sub",
        "https://[invalid/sub",
        "https://fcm.googleapis.com/" + ("x" * 4100),
    ):
        response = client.post(
            "/api/push/subscribe",
            headers=headers,
            json={
                "subscription": {
                    "endpoint": endpoint,
                    "keys": {"p256dh": "test-key", "auth": "test-auth"},
                }
            },
        )
        assert response.status_code == 400

    for endpoint in (
        "https://web.push.apple.com/test",
        "https://updates.push.services.mozilla.com/test",
    ):
        response = client.post(
            "/api/push/subscribe",
            headers=headers,
            json={
                "subscription": {
                    "endpoint": endpoint,
                    "keys": {"p256dh": "test-key", "auth": "test-auth"},
                }
            },
        )
        assert response.status_code == 200, response.text


def test_reports_include_losses_and_csv_escapes_formula_cells(client):
    assert login(client).status_code == 200
    con = database.get_db()
    try:
        con.execute(
            """
            INSERT INTO positions (
                event, sport, market, status, total_stake, actual_pnl, settled_at
            ) VALUES (?, ?, ?, 'Settled', ?, ?, ?)
            """,
            ("=1+1", "Test", "@SUM(A1:A2)", 100, -50, datetime.now(timezone.utc).isoformat()),
        )
        con.commit()
    finally:
        con.close()

    summary = client.get("/api/reports/summary").json()
    assert summary["positions"]["net_realised_pnl"] == -50

    response = client.get("/api/reports/settlements.csv")
    assert response.status_code == 200
    assert "'=1+1" in response.text
    assert "'@SUM(A1:A2)" in response.text


def test_account_reconciliation_records_signed_audited_adjustment(client):
    assert login(client).status_code == 200
    headers = {"Origin": "http://testserver"}
    con = database.get_db()
    try:
        account_id = con.execute(
            "SELECT id FROM accounts WHERE owner = 'Hunta' AND bookmaker = 'Sportsbet'"
        ).fetchone()["id"]
    finally:
        con.close()

    opened = client.post(
        f"/api/accounts/{account_id}/opening-balance",
        headers=headers,
        json={"amount": 100},
    )
    assert opened.status_code == 200
    adjusted = client.post(
        f"/api/accounts/{account_id}/reconcile",
        headers=headers,
        json={"actual_balance": 82.5, "reason": "Checked bookmaker statement"},
    )
    assert adjusted.status_code == 200
    assert adjusted.json()["adjustment"] == -17.5
    assert adjusted.json()["balance"] == 82.5

    same = client.post(
        f"/api/accounts/{account_id}/reconcile",
        headers=headers,
        json={"actual_balance": 82.5, "reason": "Repeated balance check"},
    )
    assert same.status_code == 200
    assert same.json()["adjustment"] == 0

    con = database.get_db()
    try:
        entry = con.execute(
            """
            SELECT amount, description FROM account_transactions
            WHERE account_id = ? AND transaction_type = 'reconciliation_adjustment'
            """,
            (account_id,),
        ).fetchall()
        audit_count = con.execute(
            "SELECT COUNT(*) AS total FROM audit_log WHERE action = 'account_balance_reconciled'"
        ).fetchone()["total"]
    finally:
        con.close()
    assert len(entry) == 1
    assert entry[0]["amount"] == -17.5
    assert audit_count == 1


def test_authenticated_user_can_create_and_list_integrity_checked_backup(client, monkeypatch, tmp_path):
    assert login(client).status_code == 200
    backup_dir = tmp_path / "manual-backups"
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))
    headers = {"Origin": "http://testserver"}

    created = client.post("/api/backups/create", headers=headers, json={})
    assert created.status_code == 200, created.text
    result = created.json()
    backup_file = backup_dir / result["name"]
    assert backup_file.is_file()
    assert result["size_bytes"] > 0

    listed = client.get("/api/backups")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["name"] == result["name"]

    con = database.get_db()
    try:
        audit = con.execute(
            "SELECT details FROM audit_log WHERE action = 'database_backup_created'"
        ).fetchone()
        assert audit is not None
        assert "hunta" in audit["details"]
    finally:
        con.close()


def test_non_finite_financial_inputs_are_rejected(client):
    assert login(client).status_code == 200
    headers = {"Origin": "http://testserver"}
    con = database.get_db()
    try:
        account_id = con.execute(
            "SELECT id FROM accounts WHERE owner = 'Hunta' AND bookmaker = 'Sportsbet'"
        ).fetchone()["id"]
    finally:
        con.close()

    opening = client.post(
        f"/api/accounts/{account_id}/opening-balance",
        headers={**headers, "Content-Type": "application/json"},
        content='{"amount": Infinity}',
    )
    assert opening.status_code == 422

    fractional_cent = client.post(
        f"/api/accounts/{account_id}/opening-balance",
        headers=headers,
        json={"amount": 10.001},
    )
    assert fractional_cent.status_code == 422

    scan = client.post(
        "/api/scan",
        headers=headers,
        content='{"sport":"Test","event":"Non-finite input","market":"Winner","outcomes":[{"selection":"A","bookmaker":"Book A","odds":NaN},{"selection":"B","bookmaker":"Book B","odds":2.0}],"total_stake":100}',
    )
    assert scan.status_code == 422


def test_open_position_cap_is_fifteen_and_is_enforced(client):
    assert login(client).status_code == 200
    with database.get_db() as con:
        for index in range(main.MAX_OPEN_POSITIONS - 1):
            con.execute(
                """
                INSERT INTO positions (event, sport, market, status, total_stake)
                VALUES (?, 'Test', 'Winner', 'Open', 0.01)
                """,
                (f"Cap fixture {index}",),
            )
        main.check_new_position_allowed(con, 0.01)

        con.execute(
            """
            INSERT INTO positions (event, sport, market, status, total_stake)
            VALUES ('Cap fixture final', 'Test', 'Winner', 'Open', 0.01)
            """
        )
        with pytest.raises(main.HTTPException) as error:
            main.check_new_position_allowed(con, 0.01)
        assert error.value.status_code == 409
        assert "15" in error.value.detail

    assert client.get("/api/config").json()["concurrent_position_limit"] == 15
    assert any(row["bookmaker"] == "TAB" for row in client.get("/api/accounts").json())



def test_review_headers_and_connection_cleanup(client, monkeypatch):
    import sqlite3
    from fastapi import HTTPException
    connections = []
    real_db = main.db
    def tracked():
        con = real_db()
        connections.append(con)
        return con
    monkeypatch.setattr(main, "db", tracked)
    response = client.get("/api/accounts")
    assert response.status_code == 401
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    main.accounts()
    with pytest.raises(HTTPException):
        main.set_opening_balance(-1, main.OpeningBalance(amount=1), None)
    for con in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            con.execute("SELECT 1")


@pytest.mark.parametrize("value", ["yes", "true", 1])
def test_review_manual_acceptance_requires_actual_boolean(value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        main.PositionLeg(account_id=1, event_id="e", market_id="m", outcome_id="a",
                         selection="A", odds=2.1, stake=10, manually_accepted=value)


def test_auth_throttling_precedes_password_hashing(client, monkeypatch):
    from app import rate_limits
    calls = []
    def consume(scope, identity, limit):
        calls.append(scope)
        return 17 if scope == "auth-global" else 0
    monkeypatch.setattr(rate_limits, "consume", consume)
    from types import SimpleNamespace
    monkeypatch.setattr(main, "password_hasher", SimpleNamespace(verify=lambda *args: pytest.fail("Throttled request verified a password")))
    response = login(client)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"
    assert response.headers["cache-control"] == "no-store"
    assert calls == ["auth-global"]


def test_odds_throttling_requires_auth_and_never_calls_provider(client, monkeypatch):
    assert client.get("/api/odds/sports").status_code == 401
    assert login(client).status_code == 200
    monkeypatch.setattr(main.rate_limits, "consume", lambda *args: 23)
    monkeypatch.setattr(main, "fetch_json", lambda *args, **kwargs: pytest.fail("Throttled request called provider"))
    response = client.get("/api/odds/sports")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "23"


def test_untrusted_forwarded_scheme_cannot_change_origin_validation(client):
    response = client.post("/api/auth/login", headers={"Origin": "https://testserver", "X-Forwarded-Proto": "https"},
                           json={"username": "hunta", "password": "unused"})
    assert response.status_code == 403


def test_account_journal_is_append_only(client):
    import sqlite3
    from contextlib import closing
    with closing(database.get_db()) as con, con:
        account = con.execute("SELECT id FROM accounts LIMIT 1").fetchone()[0]
        con.execute("INSERT INTO account_transactions(account_id, transaction_type, amount) VALUES (?, 'deposit', 10)", (account,))
    for statement in ("DELETE FROM account_transactions", "UPDATE account_transactions SET amount = 999",
                      "INSERT OR REPLACE INTO account_transactions SELECT * FROM account_transactions"):
        with closing(database.get_db()) as con, con:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute(statement)
            assert con.execute("SELECT amount FROM account_transactions").fetchone()[0] == 10


def test_overflowing_feed_calculation_is_skipped(client, monkeypatch):
    assert login(client).status_code == 200
    monkeypatch.setattr(main, "fetch_json", lambda *args, **kwargs: ([], {}))
    monkeypatch.setattr(main, "build_h2h_candidates", lambda *args: [{
        "sport": "Tennis", "event": "Extreme A v B", "market": "h2h", "event_id": "extreme",
        "outcomes": [{"selection": s, "bookmaker": s, "odds": 1e308} for s in ("A", "B")],
    }])
    response = client.get("/api/odds/scan/tennis_atp")
    assert response.status_code == 200
    assert response.json()["qualified_count"] == 0
