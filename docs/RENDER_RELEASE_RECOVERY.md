# Render production release recovery

This runbook is for a deployment where GitHub CI passes but the live SaaS smoke monitor reports a stale API release or `/api/health/ready/` returns HTTP 503. A green source-level CI run is not proof that Render has deployed the same backend revision.

## 1. Deploy the backend from the current release

1. Open the Render dashboard and select the **Django/API web service** (not the frontend or runner).
2. Confirm the connected repository is `rezaSarfaraz257-code/Developer-OS-Pro-4.0.0` and the deploy branch is `master`.
3. Trigger **Manual Deploy → Deploy latest commit**. Do not deploy an old commit or a side branch.
4. Wait for the deploy to finish, then inspect the API release identity:
   - `GET /api/health/live/`
   - `GET /api/health/`
   - `GET /api/health/ready/`
5. Confirm `release_sha` is current or that the deployed Backend tree matches current `master`. The scheduled Live SaaS Smoke Monitor performs this check automatically.

## 2. Readiness failures are actionable, not cosmetic

The production readiness contract intentionally requires all of these checks to be true:

- PostgreSQL connection and `DATABASE_URL`
- Non-development Django secret key
- Exact, non-wildcard production `ALLOWED_HOSTS`
- Real SMTP email backend and host configuration
- Redis-backed shared cache via `CACHE_URL`
- A fresh background-worker heartbeat

A 503 response means the service is not ready for production traffic. Do not disable readiness checks, substitute SQLite/in-memory cache, or switch production email to the console backend to force a green status.

## 3. Verify service configuration

In the Render environment settings for the API, verify the following are set to valid values for the actual deployed services:

- `ENVIRONMENT=production`
- `DEBUG=false`
- `DJANGO_SECRET_KEY` (strong secret, stored only in Render)
- `DATABASE_URL` (managed PostgreSQL internal URL when available)
- `CACHE_URL` (reachable Redis URL)
- `ALLOWED_HOSTS` (API host, exact hostnames only)
- `FRONTEND_URL` (HTTPS frontend origin)
- `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and appropriate TLS settings

Do not paste secrets into GitHub issues, PRs, logs, or chat. Never commit these values to the repository.

## 4. Worker and runner are separate concerns

The durable background worker must start against the same production database and cache as the API. Verify its logs show successful startup and a recent heartbeat; restarting only the API will not repair a missing worker.

The Web IDE runner is a separate security boundary. The current Render runner definition requests strict Bubblewrap namespace isolation. If the host runtime cannot create the required Linux namespaces, execution must remain disabled. **Do not change the runner to a permissive/shared-container mode** to make Run appear successful. Move execution to a runtime that supports the required isolation (for example, dedicated sandboxed compute with enforced CPU/memory/time limits, filesystem restrictions, and network denial) before enabling arbitrary code execution.

## 5. Final live acceptance

After deployment and configuration changes:

1. Check liveness, health, and readiness directly.
2. Open the GitHub Actions **Live SaaS Smoke Monitor** and run it on `master` (or wait for the scheduled run).
3. Require all 13 read-only checks to pass, including API release identity, readiness, frontend IDE routes, security headers, cache policy, pricing contract, and Stripe configuration.
4. Treat billing as unconfigured until real Stripe price IDs and webhook validation are set up; do not use fabricated successful-payment results.
5. Perform the full release acceptance in `FINAL_RELEASE.md` against the deployed environment.

The monitor workflow is `.github/workflows/live-saas-smoke.yml`. It writes a summary and a machine-readable JSON artifact for diagnosis. A passing PR check validates the monitor's tests only; it does not prove the live service is healthy.
