# Live SaaS Acceptance Runbook

This workflow is the final **deployed-environment acceptance test**, separate from ordinary CI. It sends real HTTP requests to the configured deployment and creates uniquely named disposable test accounts and a workspace. Run it only against a deployment you own/control, preferably a staging deployment first.

## Protected GitHub environment

Create a GitHub environment named `production` and add these environment secrets:

- `PRODUCTION_BASE_URL`: the **backend/API origin**, e.g. `https://your-api-host.example` (origin only; the script appends `/api`).
- `PRODUCTION_FRONTEND_URL`: the frontend origin, e.g. `https://your-frontend-host.example`.
- `PRODUCTION_BILLING_WEBHOOK_SECRET`: the deployed webhook signing secret used by the application's webhook verifier.

Never commit these values or print them in workflow logs. Use the same exact webhook secret configured in the deployed backend. Do not use a live payment provider's secret key here; this is the webhook signing secret.

## Run

1. Confirm the frontend and backend are deployed from the intended commit and their migrations completed.
2. In GitHub Actions, select **Production Black-Box Validation** and choose **Run workflow** on the branch containing this change.
3. Inspect the complete job log and the final `PRODUCTION VALIDATION PASS` JSON.
4. Preserve the run URL, commit SHA, deployment release identifier, and result as acceptance evidence.

The workflow fails closed when any required secret is missing. It checks the frontend root and both IDE route forms, API health/readiness and security headers, pricing/entitlements, referral attribution, Free-plan enforcement, workspace file creation, runner execution and process lifecycle, and webhook signature rejection/idempotency/plan entitlement.

## What a green result means — and does not mean

A green run is evidence for the exact endpoints and contracts exercised by this script at the time of the run. It is **not** a penetration test, does not prove tenant isolation against hostile code, and does not prove email delivery, real Stripe-provider delivery, browser UI correctness, WebSocket terminal/collaboration behavior, debugger fidelity, AI-provider quota/failure behavior, alert delivery, or encrypted backup restoration. Those need separate real-environment evidence before closing release gate issue #8.

The billing portion submits a signed synthetic webhook event using the configured webhook secret. It verifies the deployed signature handler and idempotency contract; it does not charge a card or prove that Stripe delivered an event. Run against staging first, and inspect the disposable test subscription/account records afterwards.
