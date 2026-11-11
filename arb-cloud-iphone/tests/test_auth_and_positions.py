import pytest
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


def test_one_signed_in_person_can_record_both_confirmed_legs(client):
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
    assert "No bookmaker bets were placed" in result["message"]
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
    balances = {
        (row["owner"], row["bookmaker"]): row["balance"]
        for row in client.get("/api/accounts").json()
    }
    assert balances[("Hunta", "Sportsbet")] == 610
    assert balances[("Jay", "Betfair")] == 503.4


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
