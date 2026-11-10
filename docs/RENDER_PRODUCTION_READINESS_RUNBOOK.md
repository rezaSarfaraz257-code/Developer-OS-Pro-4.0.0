# Render Production Readiness Runbook

This runbook documents the production dependencies enforced by the Django readiness probe. It is intentionally fail-closed: do not make readiness green by disabling checks, using a local-memory cache, or weakening the Runner isolation contract.

## Current incident snapshot (2026-11-08)

The scheduled Live SaaS Smoke Monitor artifact reported 10/13 checks passing. The API reported:

- Database: healthy
- Secret key: configured
- Allowed hosts: valid
- Email transport: configured
- Cache: **failed**
- Background worker heartbeat: healthy

The API was running release `daed3bf4c8d843c2b568c4e154a22dfbdd5764cf`, while `master` was at `4bcd726f1cb49b9121b0baab34f97c03dd0d4a73`. The release mismatch and cache failure are separate issues.

## Required Backend service configuration

Set values in the Render Backend web service environment. Never put secret values in Git, issue comments, screenshots, or chat.

| Variable | Requirement |
|---|---|
| `ENVIRONMENT` | `production` |
| `DEBUG` | `false` |
| `DJANGO_SECRET_KEY` | Strong, unique production secret |
| `ALLOWED_HOSTS` | Explicit API host, e.g. `dos-o91y.onrender.com`; no wildcard or localhost |
| `FRONTEND_URL` | Exact HTTPS frontend origin |
| `DATABASE_URL` | PostgreSQL connection URL |
| `CACHE_URL` | Reachable Redis-compatible URL from the Render Key Value service's **internal** connection details |
| `IDE_RUNNER_URL` | Internal URL of the dedicated Runner service |
| `IDE_RUNNER_TOKEN` | High-entropy shared service token; same value on API and Runner |
| `RESEND_API_KEY` | Optional if using Resend; when present the project selects its HTTPS email backend |
| `DEFAULT_FROM_EMAIL` | Verified sender identity for the selected email provider |

The current settings map `CACHE_URL` to Django's `django.core.cache.backends.redis.RedisCache` and to the Channels Redis layer. Without `CACHE_URL`, development falls back to local-memory cache, which is not accepted by strict production readiness.

### Fix the observed cache failure

1. In Render, create or select a Redis-compatible **Key Value** service in the same region/network as the Backend service.
2. Copy its internal Redis connection URL from the provider dashboard into the Backend service's `CACHE_URL` environment variable. Use the provider-issued URL exactly; do not invent a hostname, port, password, or TLS scheme.
3. Confirm the Key Value service is running and reachable from the Backend service.
4. Redeploy/restart the Backend only after confirming the service and variable are configured.
5. Recheck `/api/health/ready/`. The expected cache check must be `true`; do not accept an HTTP 200 from liveness as proof of readiness.

## Runner security requirements

The API readiness probe requires the dedicated Runner to answer `GET /capabilities` using the configured bearer token and to report:

- Bubblewrap isolation backend and strict security level
- Execution available
- No trusted shared-container mode
- Isolated network enforcement
- Execute and process operations available

Do not set a compatibility/shared-container mode to bypass this requirement. The Runner must independently keep network egress disabled in managed production and enforce bounded resources/output. Validate Bubblewrap on the actual deployed runtime before enabling code execution.

## Background worker

The Render background worker must run the project's `worker` management command and continuously refresh the `background-worker` heartbeat. The readiness probe requires a heartbeat no older than 90 seconds and rejects stale running jobs. Confirm the worker is healthy in Render logs; do not synthesize heartbeat rows manually.

## Release alignment and verification

1. Review the intended `master` commit and its required CI/security checks.
2. Confirm the Render Backend service is linked to the correct repository and `master` branch.
3. Deploy the intended commit only after the production dependencies above are available.
4. Query `/api/health/` and compare its `release_sha` with the deployed Backend commit SHA.
5. Query `/api/health/ready/`; require HTTP 200 and every mandatory check to be true.
6. Run the scheduled/manual Live SaaS Smoke Monitor and require all checks to pass, including release revision matching.
7. Perform authenticated, non-destructive smoke tests for login, workspace list/read, and the IDE route. Do not use production write operations as a health probe.

## Safe diagnostics

The readiness response intentionally exposes boolean dependency status, not connection strings or tokens. The server emits a structured `production_readiness_failed` log with an allowlisted `failed_checks` field. Inspect that log in Render. Never print environment variable values while diagnosing.

## Rollback

If the intended release fails health or smoke verification, stop further rollout and use Render's known-good prior deploy. Preserve logs and the failed release SHA. Do not change readiness policy, weaken security settings, or enable shared-container execution as a rollback substitute.

## Acceptance criteria

- [ ] Redis-backed cache is reachable and readiness reports `cache: true`.
- [ ] Dedicated Runner passes the strict capability contract in the real Render runtime.
- [ ] Background worker heartbeat is fresh and stale-job check is clear.
- [ ] API health release SHA matches the intended deployed commit.
- [ ] API readiness returns HTTP 200 with every mandatory check true.
- [ ] Live SaaS Smoke Monitor passes all checks.
- [ ] Authenticated workspace/IDE smoke tests pass without mutating user data.
