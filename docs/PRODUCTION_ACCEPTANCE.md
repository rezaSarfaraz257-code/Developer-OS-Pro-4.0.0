# Production Acceptance Contract

This is the final operator gate after code and CI gates pass. It separates automated evidence from checks that require access to the real production provider.

## Automated and blocking

Run **Production Black-Box Validation** against the protected production environment. It must pass:

- DNS resolution for API and frontend.
- Valid trusted TLS certificates with at least 14 days remaining.
- HTTPS-only production endpoints.
- Health and readiness latency budgets.
- Frontend HTML/app-shell availability.
- Required security headers and HSTS.
- Pricing and entitlement contracts.
- Referral attribution.
- Free-plan enforcement.
- Workspace/file persistence through the API.
- Runner execution and process lifecycle.
- Signed billing webhook acceptance.
- Invalid billing signature rejection.
- Billing webhook idempotency.
- Verified Pro entitlement transition.

The workflow stores the preflight and validation output as immutable CI artifacts for 14 days.

## Provider-level evidence required before declaring Production Accepted

These checks cannot be honestly simulated by repository tests:

1. **Email delivery:** send a real verification/password-reset email to a controlled mailbox and confirm receipt, correct sender/domain, links, and delivery latency.
2. **Persistent storage:** create durable production data, perform a controlled deployment/restart, and verify the same data remains available afterward.
3. **Backup/restore:** execute the documented production backup, verify checksum, restore into an isolated target, and verify application-level records. Never restore over the live database as a smoke test.
4. **Monitoring/alerting:** confirm API/readiness/5xx/latency/worker/DB/queue/runner signals are collected and that a controlled test alert reaches the on-call destination.
5. **Rollback:** deploy a known-good immutable commit, perform a controlled rollback drill using the platform's documented procedure, and verify health, readiness, authentication, runner, billing webhook, and data integrity afterward.

## Acceptance rule

Do not mark the release Production Accepted because a document exists or because CI is green. The release is accepted only when the automated workflow is green **and** every provider-level evidence item above has a timestamp, operator, target, and result recorded in the release/incident record.
