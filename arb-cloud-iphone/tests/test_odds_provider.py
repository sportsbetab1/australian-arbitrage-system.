from datetime import datetime, timezone
import json
from email.message import Message

import pytest

from app.odds import OddsProviderError, build_h2h_candidates, fetch_json


def event_fixture(updated):
    return {
        "id": "event-1",
        "sport_key": "soccer_epl",
        "sport_title": "Soccer",
        "home_team": "Home",
        "away_team": "Away",
        "commence_time": "2026-11-20T10:00:00Z",
        "bookmakers": [
            {
                "key": "sportsbet",
                "title": "Sportsbet",
                "markets": [{
                    "key": "h2h",
                    "last_update": updated,
                    "outcomes": [
                        {"name": "Home", "price": 2.0},
                        {"name": "Draw", "price": 3.0},
                        {"name": "Away", "price": 3.5},
                    ],
                }],
            },
            {
                "key": "betfair_ex_au",
                "title": "Betfair",
                "markets": [{
                    "key": "h2h",
                    "last_update": updated,
                    "outcomes": [
                        {"name": "Home", "price": 2.2},
                        {"name": "Draw", "price": 3.6},
                        {"name": "Away", "price": 3.1},
                    ],
                }],
            },
        ],
    }


def test_h2h_candidates_choose_best_effective_price_and_ignore_stale_data():
    now = datetime.now(timezone.utc)
    updated = now.isoformat()
    candidates = build_h2h_candidates([event_fixture(updated)], now=now)

    assert len(candidates) == 1
    by_selection = {item["selection"]: item for item in candidates[0]["outcomes"]}
    assert by_selection["Home"]["bookmaker"] == "Betfair"
    assert by_selection["Draw"]["commission_pct"] == 6.0
    assert by_selection["Away"]["bookmaker"] == "Sportsbet"

    stale = event_fixture("2020-01-01T00:00:00Z")
    assert build_h2h_candidates([stale], now=now) == []

    started = event_fixture(updated)
    started["commence_time"] = now.isoformat()
    assert build_h2h_candidates([started], now=now) == []

    undated = event_fixture(updated)
    undated["commence_time"] = None
    assert build_h2h_candidates([undated], now=now) == []


def test_incomplete_fresh_market_is_not_returned():
    now = datetime.now(timezone.utc)
    event = event_fixture(now.isoformat())
    for bookmaker in event["bookmakers"]:
        bookmaker["markets"][0]["outcomes"].pop()
    assert build_h2h_candidates([event], now=now) == []


def test_fetch_json_keeps_api_key_in_request_and_reads_quota_headers():
    class FakeResponse:
        headers = {"x-requests-used": "4", "x-requests-remaining": "96", "x-requests-last": "1"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            return b'[{"sport_key":"soccer_epl"}]'

    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return FakeResponse()

    data, usage = fetch_json("secret-test-key", "sports", {"all": "false"}, opener=opener)
    assert data == [{"sport_key": "soccer_epl"}]
    assert "apiKey=secret-test-key" in seen["url"]
    assert seen["timeout"] == 12
    assert usage == {"requests_used": 4.0, "requests_remaining": 96.0, "request_cost": 1.0}


def test_fetch_json_requires_a_configured_key():
    with pytest.raises(OddsProviderError) as error:
        fetch_json("", "sports")
    assert error.value.status_code == 503


@pytest.mark.parametrize("price", [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"])
def test_nonfinite_prices_cannot_replace_valid_quotes(price):
    now = datetime.now(timezone.utc)
    event = event_fixture(now.isoformat())
    event["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = price
    candidates = build_h2h_candidates([event], now=now)
    assert len(candidates) == 1
    home = next(item for item in candidates[0]["outcomes"] if item["selection"] == "Home")
    assert home["bookmaker"] == "Betfair"
    assert home["odds"] == 2.2


@pytest.mark.parametrize("names", [["Home", "Draw", "Other"], ["X", "Y", "Z"]])
def test_wrong_team_labels_are_not_a_complete_market(names):
    now = datetime.now(timezone.utc)
    event = event_fixture(now.isoformat())
    for book in event["bookmakers"]:
        for outcome, name in zip(book["markets"][0]["outcomes"], names):
            outcome["name"] = name
    assert build_h2h_candidates([event], now=now) == []


def test_duplicate_labels_do_not_supply_a_quote():
    now = datetime.now(timezone.utc)
    event = event_fixture(now.isoformat())
    event["bookmakers"] = event["bookmakers"][:1]
    event["bookmakers"][0]["markets"][0]["outcomes"].append({"name": "Home", "price": 9.0})
    assert build_h2h_candidates([event], now=now) == []


def test_binary_market_requires_both_event_teams():
    now = datetime.now(timezone.utc)
    event = event_fixture(now.isoformat())
    event["sport_key"] = "tennis_atp"
    for book in event["bookmakers"]:
        book["markets"][0]["outcomes"] = [
            item for item in book["markets"][0]["outcomes"] if item["name"] != "Draw"
        ]
    assert len(build_h2h_candidates([event], now=now)) == 1
    event["away_team"] = "Other"
    assert build_h2h_candidates([event], now=now) == []



@pytest.mark.parametrize("field,value", [("bookmakers", None), ("bookmakers", {}),
                                        ("home_team", {}), ("sport_title", "x" * 101)])
def test_malformed_events_do_not_crash_or_supply_candidates(field, value):
    now = datetime.now(timezone.utc)
    event = event_fixture(now.isoformat())
    event[field] = value
    assert build_h2h_candidates([None, "bad", event], now=now) == []


def test_malformed_nested_records_do_not_hide_valid_events():
    now = datetime.now(timezone.utc)
    event = event_fixture(now.isoformat())
    event["bookmakers"].extend([None, {"title": "Invalid", "markets": None}])
    event["bookmakers"][0]["markets"].append(None)
    assert len(build_h2h_candidates([None, event], now=now)) == 1


def test_malformed_outcome_record_invalidates_that_market():
    now = datetime.now(timezone.utc)
    event = event_fixture(now.isoformat())
    for book in event["bookmakers"]:
        book["markets"][0]["outcomes"].append(None)
    assert build_h2h_candidates([event], now=now) == []


def test_sports_list_filters_invalid_records():
    from app.odds import normalise_sports
    valid = {"key": "tennis_atp", "title": "Tennis", "active": True}
    assert normalise_sports([None, {}, {**valid, "key": "../bad"}, {**valid, "active": "yes"}, valid]) == [valid]


@pytest.mark.parametrize("body", [b"x" * (2 * 1024 * 1024 + 1), b"[" * 2000])
def test_provider_response_limits_and_nested_json_are_sanitized(body):
    from io import BytesIO
    class Response(BytesIO):
        headers = {}
    with pytest.raises(OddsProviderError) as error:
        fetch_json("secret-sentinel", "sports", opener=lambda *args, **kwargs: Response(body))
    assert error.value.status_code == 502
    assert "secret-sentinel" not in str(error.value)


def test_nonfinite_quota_headers_are_ignored():
    from io import BytesIO
    class Response(BytesIO):
        headers = {"x-requests-used": "NaN", "x-requests-remaining": "Infinity", "x-requests-last": "-1"}
    assert fetch_json("test-key", "sports", opener=lambda *args, **kwargs: Response(b"[]"))[1] == {}


def test_overflowing_timestamp_is_ignored():
    now = datetime.now(timezone.utc)
    event = event_fixture(now.isoformat())
    event["commence_time"] = "0001-01-01T00:00:00+23:00"
    assert build_h2h_candidates([event], now=now) == []
