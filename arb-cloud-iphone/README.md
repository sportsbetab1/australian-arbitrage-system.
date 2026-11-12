# Australian Arbitrage Control

Private mobile dashboard and API for calculating candidate arbitrage positions, tracking manually placed bets, and recording settlement. It is a manual control system: it does not place bets or connect to bookmaker accounts.

## Current operating rules

These values describe the backend constants. They are planning controls, not balances read from bank or bookmaker accounts.

- Combined starting bankroll reference: AUD 10,000
- Hunta allocation reference: AUD 4,500
- Jay allocation reference: AUD 4,500
- Settlement buffer: AUD 1,000, with a 24-hour overdue pause
- Maximum open exposure: AUD 7,500 (75% of the reference bankroll)
- Protected reserve reference: AUD 2,500
- Minimum calculated worst-case net ROI: 1.5%
- Profit allocation after positive settled P/L: 75% reinvestment and 25% withdrawal
- Betfair commission: currently assumed to be 6% on winnings, using a simplified model
- Concurrent positions: capped at 15 open/unmatched positions and by the AUD 7,500 exposure limit; each Betfair account is also limited to one unsettled leg.
- Bookmaker accounts are seeded for Sportsbet, Betfair, PointsBet, Unibet, Dabble, Picklebet, Ladbrokes, and TAB.
- Position recording: binary/two-outcome markets only

## Sign-in and assisted vetting

The dashboard supports Hunta and Jay accounts with Argon2 password hashes, HTTP-only sessions, a one-time first-run setup token, a 12-hour session lifetime, and a 15-minute lock after five failed sign-in attempts. Set a long random `AUTH_SETUP_TOKEN` in the hosting environment before first startup. The token is only for initial setup; password recovery is currently an administrator task.

Concurrent sign-in attempts update failure counts under a serialized database
transaction. Account status and password hash are rechecked after verification
before a session is issued. Deactivated users cannot use existing sessions or
receive newly queued push events; pending deliveries to inactive recipients are
marked dead when the delivery worker checks them. A delivery already handed to
the external push provider cannot be recalled. API responses, including sign-in
errors and CSV reports, use `Cache-Control: no-store`.

Both signed-in users can maintain shared bookmaker records and record either
person's manually accepted legs; the owner field identifies the bookmaker
account, not a separate access role. Notifications and push subscriptions remain
user scoped. Administrator password changes still require explicit session
revocation in the database; a self-service recovery flow is not implemented.

A qualified candidate exposes the **Open assisted vetting** button. The checklist asks the signed-in person to check the market, rules, outcomes, current odds, commission, limits, and selected accounts. One signed-in person may confirm and record both legs; a second person is not required. The user places both bets manually and, for each accepted leg, records the odds and stake the bookmaker actually accepted. The position calculation and account ledger use those entered accepted values. If actual accepted prices fall below the 1.5% candidate threshold, the system still records the placed legs and flags the lower calculated return so exposure and settlement remain tracked.

If exactly one leg was accepted, **Record one accepted leg as unmatched** stores the exposure, counts it against the cap, marks the rejected leg as not placed, and pauses new positions. One or more later manually placed hedges can be recorded on that position, but each must improve the calculated worst-case return. The app checks recorded balance and exposure. New positions remain paused until all accepted legs settle. The app cannot verify bookmaker acceptance or place the hedge itself.

## Current limitations

- The Odds API is integrated as an on-demand Australian (`au`) head-to-head feed. The dashboard can list sports and scan a selected sport; fresh, complete, upcoming-event outcome sets are scored against the 1.5% theoretical ROI threshold; events with missing or elapsed start times are excluded and shown for manual review. The API key is read from `ODDS_API_KEY`; it is not configured by the repository, and provider results are not guaranteed to match your accounts, bookmaker rules, liquidity, or prices at placement. `/api/scan` also supports manually entered odds.
- No automated bookmaker bet placement or bookmaker-account connection exists.
- Bookmaker balances are entered and tracked manually. Opening balances, deposits, withdrawals, accepted stakes, settlement returns, and reasoned balance reconciliations are journaled and audited; balances are not synced with bookmakers and must be checked against statements.
- Position tracking is binary only. The candidate calculator may calculate other outcome counts, but the assisted position workflow does not support them.
- Betfair commission is hard-coded by bookmaker name and approximated; verify the actual commission and market rules manually.
- In-app notifications are stored per user for opened, unmatched, partial-leg settlement, and final settlement events, with independent read state. Push subscriptions are stored per signed-in user; delivery endpoints must use HTTPS on Google FCM, Apple Web Push, or Mozilla Push Services hosts. Push events enter a persistent outbox and are attempted immediately when VAPID keys are configured. Failed attempts retry with exponential backoff for up to 10 attempts; exhausted deliveries are marked dead for review. Signed-in users can review queue counts and sanitized dead-letter errors at `/api/push/delivery-status`. Email alerts are not wired to these events.
- Settlement summary and CSV export are available in the authenticated dashboard.
- The authenticated dashboard can create and list integrity-checked SQLite snapshots; the restore script validates its source and preserves a pre-restore safety copy. Backups remain manual and on the same storage volume by default; off-site storage, automated scheduling, and a production restore drill remain outstanding.
- Password recovery, monitoring, and production readiness work remain outstanding.
- Do not deploy this branch for real-money use. It is under development and no deployment is performed by the repository checks.

## Development

The Render blueprint uses `arb-cloud-iphone` as its repository root directory
and `/api/health` for its health check. It is configuration only; applying it
still requires a separate hosting deployment.

Python 3.11+:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. For local development set `AUTH_COOKIE_SECURE=false` in the environment. For HTTPS hosting, use secure cookies and keep all secrets in the host's private environment settings.

Run the isolated test suite from this directory:

```bash
pytest -q
```

To retry due push notifications, schedule this command on the host (for example, every minute). The application attempts each new event immediately; the command retries failures with backoff:

```bash
python -m app.push_delivery --limit 100
```

The script does not create its own scheduler. If VAPID keys are missing, queued messages remain pending without consuming an attempt.

GitHub Actions runs Python compilation, the isolated pytest suite, and a JavaScript syntax check on pull-request and development-branch updates. The workflow contains no deploy step.

## Intended workflow

1. Enter candidate odds and run the scanner.
2. Manually verify that prices are current, the event and settlement rules match, all outcomes are covered, and funds are available.
3. Open assisted vetting and complete the checklist.
4. Manually place both legs at the bookmakers and verify both acceptances.
5. Record the position.
6. Enter each leg's actual return after settlement. Exposure remains committed until both legs are recorded.
7. Review the resulting P/L and the 75/25 allocation.

A displayed ROI is theoretical and depends on the entered prices, identical settlement conditions, commission assumptions, and successful acceptance of both bets. It is not a profit guarantee.

## Recommended next work

1. Continue validating unmatched recovery and review partial-cashout requirements. Full-leg cashouts can now be recorded manually; partial cashouts are not supported. Unmatched recovery still pauses new positions and requires each manually accepted hedge to improve the worst-case result.
2. Verify The Odds API coverage, event/market matching, provider freshness, and bookmaker-specific rules in a controlled review before relying on feed candidates.
3. Connect email alerts to the existing event notifications and schedule the push retry command on the host.
4. Reconcile manually entered bookmaker balances against statements and add adjustment/audit controls.
5. Schedule backups to an off-site destination and run a documented restore drill.
6. Expand security tests for session expiry, logout, lockout, origin checks, authorization boundaries, and recovery.
7. Complete a security and recovery review before any deployment.

Horse racing is a later phase and needs separate handling for dead heats, deductions, non-runners, starting-price/fixed-odds differences, and place terms.

## Record a completed full cashout

For an accepted, open leg, choose **Record full cashout** after the bookmaker
has fully closed that leg. Enter the final net AUD credit, including returned
stake and after all fees, provide a reference/note, and confirm it was credited.
The application records the cashout; it does not request one from a bookmaker.
Partial cashouts and cashout reversals are not supported.

`POST /api/positions/{position_id}/legs/{leg_id}/cashout` accepts
`actual_return`, `notes`, and `manual_confirmation: true`. It credits the account
once and records the user and reference in the audit log. Cashout returns are
already net of fees, so no additional commission is deducted. Ordinary
settlements continue using the existing commission calculation. The full
position exposure remains committed until every accepted leg has been closed;
then realised P/L and the existing positive-profit allocation are recorded.
The settlement CSV includes `cashout_legs` and position details expose each
leg's `close_type`. Startup adds this nullable column to existing databases;
historical closed legs without a type remain ordinary settlements.

## Feed and recovery validation

Feed quotes must contain the event's home and away teams (plus Draw for soccer),
with no duplicate or unexpected selection labels. Nonfinite prices are ignored;
an event is omitted if a valid price cannot be found for every expected outcome.
Freshness checks and manual settlement-rule verification still apply.

Backup and restore scripts explicitly close every SQLite connection before
renaming snapshot files. Stop the application and any database writers before
restoring. Restore failures preserve the original database and a pre-restore
safety copy, and remove the temporary restore file. Run a restore drill against
an isolated copy before relying on the process in production.

CI includes Windows backup/restore and feed tests alongside the complete Linux
suite. Session regressions cover expiry, logout token revocation, lockout, and
rejection of repeated first-run setup.

