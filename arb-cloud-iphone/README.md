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
The repository keeps the application in `arb-cloud-iphone`; the blueprint sets
that directory as its service root. Render generates `ADMIN_PASSWORD`; retrieve
it from the service environment settings and sign in with username `admin`.
All routes except the minimal health check require HTTP Basic authentication
when this password is set. Always use HTTPS. For local development, set these
variables explicitly in your shell; `.env.example` is documentation, not an
automatically loaded configuration file. Local development has no login when
`ADMIN_PASSWORD` is unset. Do not expose that mode publicly.

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

The shared administrator password is intended for private single-owner testing.
Individual accounts, rate limiting, and credential rotation remain future work.

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
- Replace shared administrator credentials with individual accounts and sessions.
- Build exchange commission and racing-specific models (dead heats, deductions,
  non-runners, SP/fixed odds, place terms) before phase 2.
- Extend ledger exports with immutable audit history and reconciliation.

## Ledger reports

`GET /api/reports/summary` returns recorded set counts, open exposure, and realised
P/L from all settled records. `GET /api/reports/sets.csv` exports the complete ledger,
including notes and actual P/L. The dashboard links to the download. Text that
could become a spreadsheet formula is prefixed with an apostrophe; numeric
profit/loss values retain their numeric representation.

Candidate qualification uses `net_roi`, the worst outcome after rounding stakes
to cents. `roi` remains the idealised equal-payout figure for API compatibility.
The input model rejects nonfinite numbers, fractional-cent stakes, blank labels,
and duplicate outcome selections. The app still relies on manual verification
that outcomes cover the entire market and have identical settlement rules.

## Development checks

From `arb-cloud-iphone`, after installing `requirements.txt`, run:

```bash
python -m unittest discover -s tests -v
```

The exposure ledger records the submitted stake and profit after stake rounding.
An opportunity can be opened only once, including after settlement. Opening a set
uses a serialized SQLite transaction so simultaneous requests cannot exceed capacity
or record the same opportunity twice. Tests use isolated temporary databases and
do not send notifications or place bets.

