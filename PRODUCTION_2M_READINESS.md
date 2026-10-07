# Production 2M AFN Readiness Gate

This document defines the evidence required before calling Developer OS a deployment-validated commercial release.

## Hard gates

1. **Deployment health**
   - `/api/health/` returns 200.
   - `/api/health/ready/` returns 200 and reports database, cache and production configuration healthy.
2. **IDE persistence**
   - Workspace creation works.
   - File create/write/rename works with revision control.
   - The created file remains available after a fresh workspace read.
3. **Runner**
   - Python runtime is advertised and executable.
   - `python3 main.py` returns the exact expected stdout.
   - Long-running process start/list/stop all work.
   - Runner errors are surfaced as structured HTTP errors, never generic frontend crashes.
4. **Plan enforcement**
   - Free users cannot create Team/Enterprise organization features.
   - Capability responses match server-side entitlement state.
   - Usage limits remain server-enforced.
5. **Referral**
   - Referral code generation is stable.
   - Registration attribution is persisted.
   - Qualification remains server-side and anti-abuse gated.
6. **Billing**
   - Stripe webhook signature verification passes with the real production secret.
   - The same event is idempotent on replay.
   - A valid subscription event changes entitlement server-side.
7. **E2E**
   - All checks above run against the deployed URL, not only against mocks or source inspection.

## Automated gate

Run:

```bash
PRODUCTION_BASE_URL=https://your-deployment PRODUCTION_BILLING_WEBHOOK_SECRET=whsec_... node scripts/production_validate.mjs
```

GitHub Actions also exposes the same black-box validation through
`.github/workflows/production-e2e.yml` when the two repository secrets are configured.

## Commercial-readiness rule

The codebase can be engineered toward a **2,000,000 AFN target valuation**, but the valuation is not created by a label in the repository. The strongest evidence is:

- repeatable production validation,
- stable IDE/Runner execution,
- enforceable paid entitlements,
- verified billing lifecycle,
- abuse-resistant referral mechanics,
- reliable E2E regression coverage,
- and real users/revenue after launch.

Until the deployed black-box gate passes, production readiness remains **pending external deployment validation** rather than being represented as 100%.
