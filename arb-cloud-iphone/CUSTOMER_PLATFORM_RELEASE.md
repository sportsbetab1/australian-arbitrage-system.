# Customer platform security and release gates

The customer platform is **pre-release**. Existing Hunta/Jay operator endpoints
remain a separate legacy application and are **not** tenant-scoped. Never expose
those operator endpoints to customers.

## Current customer API

- POST `/api/customer/login`: Argon2 verification, hashed 12-hour session
  token, secure HttpOnly Strict cookie, five-failure temporary lockout.
- POST `/api/customer/logout`: server-side session revocation.
- GET `/api/customer/me`, `/accounts`, `/accounts/{id}`, `/positions`,
  `/positions/{id}`, `/bankroll`, `/summary`: all read through the
  authenticated session's tenant ID, never a tenant ID from query parameters.
- POST `/api/customer/accounts`: owner-only account registration.
- POST `/api/customer/bankroll`: owner-only integer-cent ledger writes,
  audit records, duplicate-reference protection.
- GET `/api/customer/audit`: owner-only audit history.

The customer summary intentionally reports `verified_bankroll=false` and
`new_positions_allowed=false`. The indicative exposure calculation is **not**
an authorization to place a wager.

## Required before public launch

1. Run the complete test suite and verify the GitHub Actions status is green.
2. Implement safe customer onboarding, email verification, account recovery,
   password changes and session invalidation after password rotation.
3. Add per-IP rate limiting in addition to per-account lockout; review
   enumeration risks and concurrent login behavior.
4. Enforce account and bankroll verification before any position approval.
5. Implement tenant-scoped draft, approval, placement and settlement workflows,
   with cross-tenant integration tests for every mutation.
6. Implement subscription billing and entitlements. Define the proposed
   10% profit fee, refunds, disputes and calculation methodology.
7. Implement dual approval for sensitive platform-owner actions; confirm
   the exact operator approval policy before building it.
8. Review Australian gambling, financial, privacy and consumer-law obligations
   with qualified advisers before marketing or accepting payments.
9. Establish encrypted backups, restore tests, audit monitoring, secrets
   management, HTTPS and production security headers.
10. Conduct a third-party penetration test and staged rollout.

## Development tests

From `arb-cloud-iphone`, after installing the application requirements and
`httpx>=0.27,<0.29`:

```sh
python -m unittest discover -s tests -p 'test_customer_schema.py' -v
python -m unittest discover -s tests -p 'test_customer_api.py' -v
python -m unittest discover -s tests -p 'test_auth_boundary.py' -v
python -m unittest discover -s tests -p 'test_risk_policy.py' -v
python -m unittest discover -s tests -p 'test_frontend_contract.py' -v
```

All customer tests use temporary or in-memory SQLite databases and do not
place wagers. Do not mark a feature verified merely because a test file exists.
