# Deployment handoff

The reviewed application is on `main`. The deployment-readiness follow-up adds
shared authentication/odds request budgets, trusted-proxy origin handling,
read-only host preflight, and a disposable localhost acceptance check.

Local verification: 103 pytest tests passed. The real localhost smoke command
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
