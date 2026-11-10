# Australian Arbitrage Control — cloud + iPhone starter

This is a deployable starter application, not a live odds service and not a betting bot.
It provides a cloud API, iPhone-installable PWA dashboard, candidate arbitrage calculator,
five-set exposure guard, manual confirmation workflow, opportunity log, and optional
email / web-push notifications.

## Configured limits
- Starting bankroll: AUD 10,000 (planning figure; not connected to financial accounts)
- Total stake per arbitrage set: AUD 500
- Maximum simultaneous open sets: 5
- Maximum open exposure: AUD 2,500
- Minimum theoretical net ROI threshold: 1.5%
- Sports phase: soccer, tennis, basketball/NBA, NRL, AFL, cricket
- Horse racing: phase 2
- Execution: manual confirmation only

## Critical current limitation
The app does NOT fetch live bookmaker odds. `/api/scan` accepts candidate odds submitted
to the API / entered in the dashboard. Before live scanning, choose an authorised odds-data
provider that expressly permits your intended use, obtain credentials, and implement its
documented API adapter. Do not scrape bookmaker pages, bypass access controls, or assume
that a bookmaker permits arbitrage. This app does not place bets.

## Run locally
Python 3.11+ recommended.

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. API docs: http://127.0.0.1:8000/docs

## Cloud deployment (Render example)
1. Create a private GitHub repository and upload this project.
2. Create a Render account and connect the repository.
3. Render can use `render.yaml` as a blueprint. The persistent disk stores SQLite data.
4. Set email SMTP credentials in Render's environment settings. Use an email provider that
   supports authenticated SMTP or adapt `send_email()` to its official API.
5. Generate VAPID keys (for example with `pywebpush` tooling) and set
   `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_SUBJECT` as secrets.
6. Deploy and use the HTTPS URL.

For production scale, move from SQLite to managed PostgreSQL, add authentication,
rate limiting, audit logs, backups, monitoring and secret rotation before exposing it
beyond a private test deployment.

## Install on iPhone
1. Open the deployed HTTPS site in Safari.
2. Tap Share → Add to Home Screen.
3. Launch it from the Home Screen.
4. Tap "Enable iPhone push notifications" and allow notifications.

iOS web push is supported for Home Screen web apps on iOS/iPadOS 16.4+.
Push only works after VAPID keys are configured and the device has subscribed.
Email only works after SMTP variables are configured.

## API candidate example
POST `/api/scan`:

```json
{
  "sport": "Soccer",
  "event": "Example FC v Sample United",
  "market": "90-minute match result",
  "total_stake": 500,
  "outcomes": [
    {"selection":"Home","bookmaker":"Operator A","odds":2.15,"commission_pct":0},
    {"selection":"Draw","bookmaker":"Operator B","odds":3.6,"commission_pct":0},
    {"selection":"Away","bookmaker":"Operator C","odds":4.2,"commission_pct":0}
  ]
}
```

The example values are illustrative only. Only submit prices that are current and available
to your account. The calculator assumes mutually exclusive, collectively exhaustive outcomes
and identical settlement conditions. Commission is approximated on net winnings and may
not represent the rules of a particular exchange market. Verify market rules and fees.

## Workflow
1. Submit candidate prices to `/api/scan`.
2. If it qualifies, verify event, market, selections, odds, stake availability, limits and
   settlement rules yourself.
3. Place both legs manually at the operators, ensuring both are accepted.
4. Only then use "I have manually verified and placed both legs" to record the set.
5. Record actual P/L when settled.

The open-set control is an exposure ledger. It does not confirm bookmaker bet acceptance.
A rejected or moved second leg can leave you with an exposed position.

## Next engineering work
- Integrate an authorised odds provider API with documented terms and data rights.
- Add market matching / settlement-rule normalisation and stale-price checks.
- Add user authentication, multi-device security and admin-only access.
- Build exchange commission and racing-specific models (dead heats, deductions,
  non-runners, SP/fixed odds, place terms) before phase 2.
- Add exportable audit and P/L reports.
