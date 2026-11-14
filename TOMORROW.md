# Deployment handoff

## Current focus: private operator app

Public paid/customer development and bookmaker account connections are paused
at the owner's request. The current task implements the owner's newer compact
phone reference: top horizontal navigation, four summary cards in one row,
7/30-day or all-time chart, paired buffer/allocation and exposure panels, key
metrics, recent positions and a fixed four-action bar. The scanner/status and
notification panels moved off the front page. No sample profit or connection
claims are hardcoded. Recent chart data is grouped by UTC settlement day; the
displayed percentage uses selected-period P/L divided by starting bankroll.
Key Metrics remain all-time. Bottom actions open actual review/calculator/
settlement workflows without asserting any bookmaker acceptance.
`static/dashboard.css` is the single stylesheet. The browser check covers five
widths, page/icon navigation, chart periods, compact phone geometry, input
preservation and the bottom actions using disposable records. The updated
regression suite passed 196 tests.
Navigation icons and labels have page URLs (`/dashboard`, `/positions`,
`/settlements`, `/accounts`, `/scanner`, `/analytics`, `/settings`, `/notifications`,
`/buffer`).
The server serves the sign-in-protected app shell at each page, with no-store
headers; private APIs remain authenticated. Navigation supports reloads,
Back/Forward and native modified-link clicks.
The owner requested Hunta-only first-run setup. The current setup screen creates
only Hunta's login. Jay can later use **Set up Jay login** and the private setup
token to create his own password; setup cannot overwrite existing or disabled
accounts. Legacy API clients can still supply both passwords for initial setup.
The staged-onboarding follow-up passed 193 tests and Chromium checks covering
Hunta-only signup, dashboard access and Jay's later signup/sign-in.
The dashboard follow-up adds section navigation, responsive account cards,
owner filtering, refresh timestamps and stale-data warnings, optional-service
failure messages, odds configuration status and single-flight odds controls.

Render Free has no persistent disk and erases SQLite data on sleep/restart.
`arb-cloud-iphone/ops/render-free-preview.yaml` therefore prepares a temporary
authenticated preview with a visible fictional-records notice, not a durable
financial deployment. The paid disk blueprint is unchanged. See
`arb-cloud-iphone/ops/RENDER_PREVIEW.md` for creation and private odds-key setup.
The owner approved a temporary free preview and connected Render. The service
is live at `https://arbitrage-private-preview.onrender.com` in Singapore on
the Free plan with no disk and automatic deployment disabled. It deploys this
branch manually; use Render's deployment history for the live commit. Prior CI
passed and the latest local verification is documented above. Render's
default Python failed dependency installation, so the host now pins 3.11.11.
`AUTH_SETUP_TOKEN` was generated privately by Render. The start command enables
preview mode and places the database and snapshots under `/tmp/arbitrage-preview`.

Live-host checks passed HTTPS health, authenticated API protection, no-store
headers, rejection of cross-origin POSTs and acceptance of HTTPS same-origin
requests up to private setup-token validation. No accounts were created by
these checks. The owner corrected `ODDS_API_KEY` in Render after a credential
mix-up. A private provider check on 2026-11-14 returned HTTP 200 for sports and
Australian NBA head-to-head odds: 71 active non-outright sports and 5 events.
The single odds request cost 1 credit, leaving 499 at that time. This verifies
provider acceptance, not an authenticated user workflow through the Render app.
Owner setup/sign-in and an in-app scan remain to be completed by the owner.
The redesigned HTML, stylesheet, mobile setup screen and manifest were checked
on the actual HTTPS host. Bookmaker account integrations remain unimplemented;
the ledger balances are manually recorded. Official provider access is needed
before building a connector. Keep all credential values out of project files.

Local checks passed: the 187-test regression suite, a separate new config
privacy test, the localhost fictional settlement/backup smoke, service-worker
checks, and headless Chromium at 390px and 1440px. Browser checks covered
setup/sign-in/sign-out, owner filtering, calculator draft preservation, core
and optional-service outages, and mocked odds sports/scan responses.

The reviewed application is on `main`. The deployment-readiness follow-up adds
shared authentication/odds request budgets, trusted-proxy origin handling,
read-only host preflight, and a disposable localhost acceptance check.

The storage follow-up adds private creation of database files, append-only
journal guards, and stronger preflight checks for actual application snapshots,
expected owner identities, and POSIX file permissions.

The feed follow-up bounds provider responses and handles malformed nested data,
sport records, labels, quota headers and extreme timestamps safely. All feed
verification uses mocked provider responses; live coverage remains unverified.

Local verification: all 123 tests passed, including malformed feed records,
extreme timestamps and calculated-return overflow. The real localhost smoke command
passed startup, authentication, rounded qualification, a fictional position and
settlement, reports, snapshot creation and logout. Compilation and dashboard
JavaScript syntax checks passed. See the deployment-readiness pull request for
final Linux/Windows CI results.

## Start here

From `arb-cloud-iphone`, with development dependencies installed:

```bash
python -m scripts.smoke_local
```

This creates its own temporary database and test-only credentials, starts a
localhost server, runs fictional operations, and cleans up afterward. It does
not use the operating database or any live provider.

For your own Windows localhost dashboard, use the existing `start-local.ps1`
and complete first-run setup with your own Hunta/Jay passwords. Do not send
passwords, setup tokens or API keys through chat or commit them.

## Decisions and access still needed

1. Choose the hosting provider/account and budget. No hosting has been created
   or paid for. The Render blueprint is configuration, not an active deployment.
2. Configure HTTPS, persistent storage, secure cookies and the trusted reverse
   proxy. Verify Origin handling and client identities on the actual host.
3. Complete your own account setup. Configure an authorized odds API key in
   private host settings if live scanning is wanted. Review provider coverage,
   price freshness and bookmaker rules before relying on candidates.
4. Configure VAPID keys for phone push and enable the prepared retry timer on
   the database host. Confirm actual delivery. Email event alerts remain unwired.
5. Enable local backups, select an encrypted off-site destination, enable
   transfer/retention and failure monitoring, then download and restore a remote
   copy in an isolated drill.
6. Run `python -m scripts.preflight` on the chosen host after setup and the first
   backup. Resolve failures and explicitly review warnings; passing this command
   does not verify remote services, scheduler activation or production readiness.
7. Rehearse the intended workflow with fictional positions on the private host,
   check all balances/P&L and settlement rules, and complete host security and
   recovery review before real-money use.

The app records manually placed bets; it never places bets or connects to
bookmaker accounts. Partial cashouts/reversals and horse racing are not
implemented. Monitoring, edge connection/body limits and host-level protection
remain configuration tasks. Detailed commands and recovery instructions are in
`arb-cloud-iphone/ops/README.md`.

## Frontend/backend integration

The backend-upgrade dashboard/API work is reconciled with main's provider,
request-budget, journal and recovery safeguards. The owner confirmed the newer
rules: no fixed position-count cap, AUD 7,500 exposure cap retained, TAB retired,
and ordinary positions below 1.5% ROI rejected. Emergency unmatched recording
remains available. Existing TAB balances/history are retained with inactive
accounts. Customer endpoints remain separately authenticated and tenant scoped;
customer trading remains disabled and the public launch gates remain open.
The alternative commercial-identity draft is still separate: its competing
identity/tenant model has not been merged into this application.

The integrated regression suite passed 186 tests before the additional TAB
retirement regression, which also passed. Browser checks used an isolated local
database and fictional credentials at mobile and desktop widths, including
setup/sign-in, live risk figures, input preservation, calculation and graceful
reporting outage handling. No live odds or bookmaker operations were performed.
