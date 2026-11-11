from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json


class OddsProviderError(Exception):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def fetch_json(api_key, path, params=None, base_url="https://api.the-odds-api.com/v4", opener=None):
    if not api_key:
        raise OddsProviderError("Live odds are disabled until ODDS_API_KEY is configured.", 503)
    query = dict(params or {})
    query["apiKey"] = api_key
    url = base_url.rstrip("/") + "/" + path.lstrip("/") + "?" + urlencode(query)
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "AustralianArbitrageControl/1.0"})
    open_request = opener or urlopen
    try:
        with open_request(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
            headers = response.headers
    except HTTPError as error:
        if error.code == 401:
            raise OddsProviderError("The odds provider rejected the configured API key.", 502) from None
        if error.code == 429:
            raise OddsProviderError("The odds provider quota or rate limit was reached.", 429) from None
        raise OddsProviderError(f"The odds provider returned HTTP {error.code}.", 502) from None
    except (URLError, TimeoutError, OSError):
        raise OddsProviderError("The odds provider could not be reached.", 503) from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise OddsProviderError("The odds provider returned invalid JSON.", 502) from None

    usage = {}
    for header, key in (
        ("x-requests-used", "requests_used"),
        ("x-requests-remaining", "requests_remaining"),
        ("x-requests-last", "request_cost"),
    ):
        value = headers.get(header)
        if value is not None:
            try:
                usage[key] = float(value)
            except (TypeError, ValueError):
                pass
    return payload, usage


def _market_updated_at(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_h2h_candidates(events, now=None, max_age_seconds=180, commission_pct=6.0):
    now = now or datetime.now(timezone.utc)
    candidates = []
    for event in events:
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                updated = _market_updated_at(market.get("last_update"))
                if updated is None:
                    continue
                age = (now - updated).total_seconds()
                if age > max_age_seconds or age < -60:
                    continue

                title = bookmaker.get("title") or bookmaker.get("key") or "Unknown bookmaker"
                key = str(bookmaker.get("key", "")).lower()
                commission = commission_pct if "betfair" in key or "betfair" in title.lower() else 0.0
                for outcome in market.get("outcomes", []):
                    name = str(outcome.get("name", "")).strip()
                    try:
                        price = float(outcome.get("price"))
                    except (TypeError, ValueError):
                        continue
                    if not name or price <= 1:
                        continue
                    effective_price = 1 + (price - 1) * (1 - commission / 100)
                    item = {
                        "selection": name,
                        "bookmaker": title,
                        "odds": price,
                        "commission_pct": commission,
                        "effective_odds": effective_price,
                        "updated_at": updated.isoformat(),
                    }
                    # Group by event, h2h market and provider outcome name.
                    slot = (event.get("id"), "h2h", name)
                    prior = best.get(slot)
                    if prior is None or effective_price > prior["effective_odds"]:
                        best[slot] = item

        # The best prices are grouped after all bookmakers are visited below.
    event_ids = {event.get("id") for event in events}
    for event_id in event_ids:
        event = next((item for item in events if item.get("id") == event_id), None)
        if not event:
            continue
        outcomes = [value for (eid, market_key, _), value in best.items() if eid == event_id and market_key == "h2h"]
        if len(outcomes) < 2 or len(outcomes) > 3:
            continue
        candidates.append({
            "sport": event.get("sport_title") or event.get("sport_key") or "Unknown sport",
            "event": (str(event.get("home_team", "")) + " vs " + str(event.get("away_team", ""))).strip(),
            "event_id": str(event_id or ""),
            "market": "h2h",
            "commence_time": event.get("commence_time"),
            "outcomes": [
                {key: outcome[key] for key in ("selection", "bookmaker", "odds", "commission_pct")}
                for outcome in outcomes
            ],
            "source_updated_at": min(outcome["updated_at"] for outcome in outcomes),
        })
    return candidates
