# Render Production Readiness Runbook

This runbook documents the production dependencies enforced by the Django readiness probe. It is intentionally fail-closed: do not make readiness green by disabling checks, using a local-memory cache, or weakening the Runner isolation contract.

## Current incident snapshot (2026-11-16)

The scheduled Live SaaS Smoke Monitor run `43815825025` failed against the Render production target. Its expected release was `a6d1b612229e391744f699cabd324cd6f52122ac`; the deployed API reported release `23f09da8a3cf3cd3ba27df666e191d1417ee6bb2`, which was 62 commits behind `master` at the time of the probe.

The live `/api/health/ready/` endpoint returned HTTP 503. Safe diagnostics identified the cache and isolated Runner readiness gates as failed. Treat these as two separate deployment dependencies; do not infer that fixing one makes the other healthy. The API process being reachable or its liveness endpoint returning HTTP 200 does not establish production readiness.

This is a live Render incident, not a PR-CI failure. Pull-request executions of the monitor run contract tests only and explicitly skip external production probes. A green pull-request monitor job is not evidence of production health. The last observed live failure remains authoritative until a scheduled/manual production probe produces a new artifact with all 13 checks passing and the deployed release identity aligned.

Historical snapshot (2026-11-08): an earlier monitor reported 10/13 checks passing, with cache failed and worker heartbeat healthy. Its release was `daed3bf4c8d843c2b568c4e154a22dfbdd5764cf` versus then-`master` `4bcd726f1cb49b9121b0baab34f97c03dd0d4a73`.

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

## Dedicated Runner service configuration and security gate

Configure the dedicated Render Runner service from the repository's `Runner/Dockerfile` and `Runner/main.py` contract:

| Variable | Required value / handling |
|---|---|
| `IDE_RUNNER_TOKEN` | Same high-entropy secret as the Backend service; never expose it in logs or client code |
| `RUNNER_SECURITY_LEVEL` | `strict` |
| `RUNNER_SANDBOX_MODE` | `bwrap`; only approve after namespace isolation works on the actual Render host |
| `RUNNER_ALLOW_NETWORK` | `false`; managed production forces network access off regardless |
| `RUNNER_TIMEOUT_SECONDS` | Keep bounded; application clamps the maximum to 120 seconds |
| `RUNNER_MEMORY_MB` | Keep within the supported 128–2048 MB range and the service's actual memory budget |
| `RUNNER_MAX_CONCURRENT` | Set conservatively for the allocated CPU/RAM; application bounds it to 1–64 |

On the Backend service, set `IDE_RUNNER_URL` to the Runner service's private/internal URL, not a guessed hostname. Keep the Runner reachable only through the intended private service path; the Backend authenticates using the bearer token.

The unauthenticated `GET /health` endpoint is only a process/health signal. It is not proof that code execution is safe or available. The Backend readiness gate authenticates to `GET /capabilities` and requires the exact strict Bubblewrap contract:

- Bubblewrap isolation backend and strict security level
- Execution available
- No trusted shared-container mode
- Isolated network enforcement
- Execute and process operations available

Do not set compatibility/shared-container mode to bypass this requirement. If Render denies the namespaces required by Bubblewrap, keep execution blocked and move the Runner to a runtime that supports per-execution isolation. Never weaken the sandbox to make readiness green.

## Background worker

The current Render configuration supports an embedded worker in the API service by default. Keep `BACKGROUND_WORKER_MODE` unset or `embedded` unless a separate worker service is intentionally deployed and verified. For a separate worker, use the same release and `DATABASE_URL`, and set `BACKGROUND_WORKER_MODE=external` on the API only after the external worker is healthy.

In either mode, the worker must refresh the `background-worker` heartbeat continuously. The readiness probe requires a heartbeat no older than 90 seconds and rejects stale running jobs. Confirm the worker startup and heartbeat in Render logs; do not synthesize heartbeat rows manually.

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

### Cache readiness failure diagnostics

The readiness probe performs a unique, short-lived write/read/delete round-trip and emits a structured, secret-safe `cache_readiness_failed` event. The log includes only the failure category and backend class; it never includes the Redis URL, password, or exception message.

| Sanitized `failure_reason` | Meaning | Corrective action |
|---|---|---|
| `cache_url_missing` | Strict Render runtime has no effective `CACHE_URL` | Set the Backend service's `CACHE_URL` to the Key Value service's provider-issued internal connection URL |
| `redis_backend_not_configured` | Django is not using `RedisCache` in strict runtime | Verify the deployed settings and environment; do not substitute local-memory cache |
| `cache_roundtrip_mismatch` | Cache write/read did not return the same probe value | Check the Key Value service health, selected database, eviction/ACL policy, and network path |
| `cache_backend_error:TimeoutError` (or another exception class) | Redis operation raised a transport/backend error | Check service availability, internal hostname/port, TLS scheme, credentials and network reachability in Render; inspect provider logs |

The probe uses a cryptographically random key and value for every request, expires the key quickly, and attempts cleanup even after an operation fails. This avoids collisions between concurrent API replicas and prevents the readiness endpoint from leaving a permanent marker.

After changing Render configuration, restart/redeploy the Backend and require all of the following:
1. The sanitized cache failure event no longer appears.
2. `GET /api/health/ready/` returns HTTP 200 with `checks.cache: true`.
3. The live smoke workflow passes readiness and release-identity checks.
4. The deployed `release_sha` matches the intended Backend revision.

Do not post raw Render logs or connection strings. Share only the failure category, backend class, HTTP status, and release SHA.
