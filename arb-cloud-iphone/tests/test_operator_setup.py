"""Staged private operator onboarding; no real credentials or provider calls."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import database, main

TOKEN = "fictional-private-setup-token-for-tests"
HUNTA = "fictional-hunta-password-2026"
JAY = "fictional-jay-password-2026"
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def fresh_client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "operators.sqlite"))
    monkeypatch.setattr(main, "AUTH_COOKIE_SECURE", False)
    monkeypatch.setenv("AUTH_SETUP_TOKEN", TOKEN)
    with TestClient(main.app) as client:
        yield client


def create_hunta(client):
    return client.post("/api/auth/setup", headers=ORIGIN,
                       json={"setup_token": TOKEN, "hunta_password": HUNTA})


def test_hunta_can_set_up_and_sign_in_without_jay(fresh_client):
    c = fresh_client
    assert c.get("/api/auth/status").json()["setup_required"] is True
    assert create_hunta(c).status_code == 200
    state = c.get("/api/auth/status").json()
    assert state["setup_required"] is False
    assert state["jay_setup_required"] is True
    with database.get_db() as con:
        assert [r[0] for r in con.execute("SELECT username FROM auth_users")] == ["hunta"]
    assert c.post("/api/auth/login", headers=ORIGIN,
                  json={"username": "jay", "password": JAY}).status_code == 401
    assert c.post("/api/auth/login", headers=ORIGIN,
                  json={"username": "hunta", "password": HUNTA}).status_code == 200
    assert c.get("/api/accounts").status_code == 200
    assert create_hunta(c).status_code == 409


def test_jay_can_join_later_without_changing_hunta(fresh_client):
    c = fresh_client
    payload = {"setup_token": TOKEN, "jay_password": JAY}
    assert c.post("/api/auth/setup/jay", headers=ORIGIN, json=payload).status_code == 409
    assert create_hunta(c).status_code == 200
    assert c.post("/api/auth/setup/jay", json=payload).status_code == 403
    assert c.post("/api/auth/setup/jay", headers={"Origin": "https://untrusted.example"}, json=payload).status_code == 403
    assert c.post("/api/auth/setup/jay", headers=ORIGIN,
                  json={**payload, "setup_token": "wrong"}).status_code == 403
    assert c.post("/api/auth/setup/jay", headers=ORIGIN,
                  json={**payload, "jay_password": "short"}).status_code == 422
    assert c.post("/api/auth/setup/jay", headers=ORIGIN, json=payload).status_code == 200
    assert c.get("/api/auth/status").json()["jay_setup_required"] is False
    assert c.post("/api/auth/setup/jay", headers=ORIGIN,
                  json={**payload, "jay_password": "replacement-password-2026"}).status_code == 409
    for username, password in (("hunta", HUNTA), ("jay", JAY)):
        assert c.post("/api/auth/login", headers=ORIGIN,
                      json={"username": username, "password": password}).status_code == 200
    with database.get_db() as con:
        con.execute("UPDATE auth_users SET is_active = 0 WHERE username = 'jay'")
    assert c.post("/api/auth/setup/jay", headers=ORIGIN, json=payload).status_code == 409


def test_concurrent_jay_setup_only_creates_one_account(fresh_client):
    assert create_hunta(fresh_client).status_code == 200

    def attempt(_):
        try:
            main.setup_jay(main.JaySetup(setup_token=TOKEN, jay_password=JAY))
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [200, 409]


def test_jay_setup_shares_auth_request_budget(fresh_client, monkeypatch):
    monkeypatch.setattr(main.rate_limits, "consume", lambda *args: 30)
    r = fresh_client.post("/api/auth/setup/jay", headers=ORIGIN,
                          json={"setup_token": TOKEN, "jay_password": JAY})
    assert r.status_code == 429
    assert r.headers["retry-after"] == "30"
