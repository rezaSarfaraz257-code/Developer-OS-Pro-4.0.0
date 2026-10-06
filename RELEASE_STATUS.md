# Release Status — Developer OS 4.0.0

## Engineering state

The 4.0.0 artifact is hardened and prepared for external production validation.

### Implemented

- SaaS organizations, roles and invitations
- subscriptions and Stripe billing lifecycle
- webhook signature verification and event idempotency
- usage metering and plan limits
- API key lifecycle
- JWT refresh rotation and logout blacklist
- GitHub OAuth with PKCE
- encrypted GitHub tokens
- audit logging
- workspace/IDE persistence
- sandboxed Runner with resource limits
- AI conversation and structured engineering actions
- production security headers
- PostgreSQL support
- Docker Compose production stack
- health/readiness endpoints
- frontend lint/build/test CI
- PostgreSQL migration/test CI
- runner container integration CI
- full-stack Docker smoke E2E
- Stripe webhook regression tests

## Validation status

| Gate | Status in this environment |
|---|---|
| Static Python compile | PASS |
| Runner unit tests | PASS |
| Static release gate | PASS |
| Django + PostgreSQL integration | REQUIRES CI |
| Frontend npm lint/build/test | REQUIRES CI |
| Docker build/runtime | REQUIRES CI |
| Full-stack E2E | REQUIRES CI |
| Production Render deployment | REQUIRES deployment environment |

The project must only be described as **100% production-validated** after the external CI workflow passes on the deployed commit.
