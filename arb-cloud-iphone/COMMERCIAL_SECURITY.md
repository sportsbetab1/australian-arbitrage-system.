# Commercial platform security and launch checklist

## Architecture
- The existing Hunta/Jay routes remain **internal only**. Customer cookies do not grant access to them.
- Customer resources live in separate tenant-keyed tables; tenant membership is checked before reads and writes.
- Customer positions are **draft records only**. No bookmaker submission, settlement or transfer occurs from customer endpoints.
- Financial amounts in the new customer schema use integer AUD cents.
- Subscription and proposed profit fee records are read-only; fee collection remains disabled pending Australian legal review.
- Account opening balances and ledger entries are user-entered records, not independently verified bookmaker balances.

## Security work before any public deployment
1. Implement verified-email onboarding, password reset and safe invitation acceptance.
2. Add CSRF tokens for all cookie-authenticated writes, independent of Origin checks.
3. Add network-level IP/device rate limiting, email verification throttles and abuse controls.
4. Verify tenant checks across every future scanner, position, settlement, notification, export, push and reporting route.
5. Add tenant-safe foreign keys for customer position legs and account references; prohibit cross-tenant links at DB level.
6. Add audited owner bootstrap, MFA and dual-approved administrative execution routes.
7. Implement payment provider webhook signature verification and subscription entitlement checks; never trust client-provided payment status.
8. Add privacy retention, data export/delete policy, secrets management, monitoring, backups and restore tests.
9. Perform independent security review, migration rehearsal and load testing.
10. Obtain specialist legal advice on Australian wagering, subscriptions and profit-based fees before activation.

## Validation
Run from arb-cloud-iphone:
```sh
python -m unittest discover -s tests -p 'test_commercial_*.py' -v
```
The CI workflow executes this command on the feature branch. Never treat an unverified workflow as passing.
