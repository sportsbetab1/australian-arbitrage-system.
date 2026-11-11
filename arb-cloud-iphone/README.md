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
- Concurrent positions: limited by the exposure cap; each Betfair account is also limited to one unsettled leg. An older project discussion mentioned a 15-position cap; the current backend does not enforce that separate cap and should be reconciled before release.
- Position recording: binary/two-outcome markets only

## Sign-in and assisted vetting

The dashboard supports Hunta and Jay accounts with Argon2 password hashes, HTTP-only sessions, a one-time first-run setup token, a 12-hour session lifetime, and a 15-minute lock after five failed sign-in attempts. Set a long random `AUTH_SETUP_TOKEN` in the hosting environment before first startup. The token is only for initial setup; password recovery is currently an administrator task.

A qualified candidate exposes the **Open assisted vetting** button. The checklist asks the signed-in person to check the market, rules, outcomes, current odds, commission, limits, and selected accounts. One signed-in person may confirm and record both legs; a second person is not required. The user places both bets manually and records whether each bookmaker accepted the displayed bet.

If exactly one leg was accepted, **Record one accepted leg as unmatched** stores the exposure, counts it against the cap, marks the rejected leg as not placed, and pauses new positions until the accepted leg is settled. The app cannot verify bookmaker acceptance. This recovery path tracks settlement of the accepted leg; it does not place an offsetting hedge or track a later hedge as another leg.

## Current limitations

- The Odds API is integrated as an on-demand Australian (`au`) head-to-head feed. The dashboard can list sports and scan a selected sport; fresh complete outcome sets are scored against the 1.5% theoretical ROI threshold and shown for manual review. The API key is read from `ODDS_API_KEY`; it is not configured by the repository, and provider results are not guaranteed to match your accounts, bookmaker rules, liquidity, or prices at placement. `/api/scan` also supports manually entered odds.
- No automated bookmaker bet placement or bookmaker-account connection exists.
- Bookmaker balances are entered and tracked manually. Opening balances, deposits, withdrawals, accepted stakes, and settlement returns are journaled; they are not synced with bookmakers and need reconciliation against statements.
- Position tracking is binary only. The candidate calculator may calculate other outcome counts, but the assisted position workflow does not support them.
- Betfair commission is hard-coded by bookmaker name and approximated; verify the actual commission and market rules manually.
- In-app notifications are stored per user for position opened/unmatched and settled events, and each user has independent read state. Push/email delivery is not yet connected to these events; existing push subscriptions are not yet separated by user.
- Settlement summary and CSV export are available in the authenticated dashboard.
- Backup and restore scripts create integrity-checked SQLite snapshots and keep a pre-restore safety copy. They are manual and on the same storage volume by default; off-site storage, automated scheduling, and restore drills remain outstanding.
- Password recovery, monitoring, and production readiness work remain outstanding.
- Do not deploy this branch for real-money use. It is under development and no deployment is performed by the repository checks.

## Development

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

1. Finish unmatched-bet recovery: add a way to record an offsetting hedge or cashout, and test repricing / rejected-leg scenarios. Current protection records one accepted leg, reserves exposure, pauses new positions, and tracks its settlement.
2. Verify The Odds API coverage, event/market matching, provider freshness, and bookmaker-specific rules in a controlled review before relying on feed candidates.
3. Harden per-user push subscriptions and add a delivery outbox/retry path before connecting push/email alerts to the in-app events.
4. Reconcile manually entered bookmaker balances against statements and add adjustment/audit controls.
5. Schedule backups to an off-site destination and run a documented restore drill.
6. Expand security tests for session expiry, logout, lockout, origin checks, authorization boundaries, and recovery.
7. Confirm whether the separate 15-position cap and TAB bookmaker from earlier project discussions remain requirements.
8. Complete a security and recovery review before any deployment.

Horse racing is a later phase and needs separate handling for dead heats, deductions, non-runners, starting-price/fixed-odds differences, and place terms.
