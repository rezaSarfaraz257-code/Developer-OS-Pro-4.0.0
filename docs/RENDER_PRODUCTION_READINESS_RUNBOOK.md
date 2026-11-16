# Render Production Readiness Runbook

This runbook documents the production dependencies enforced by the Django readiness probe. It is intentionally fail-closed: do not make readiness green by disabling checks, using a local-memory cache, or weakening the Runner isolation contract.

## Latest verified live incident (2026-11-16 22:12 UTC)

The scheduled/master Live SaaS monitor run `43966857836` completed with **10/13 checks passing and 3 failing**. The API returned release SHA `a6d1b612229e391744f699cabd324cd6f52122ac`, while the tested repository revision was `c693137951ec780f2294c2cde1b3aa481eb16918` (14 commits ahead of the deployed Backend revision at the time of comparison).

The machine-readable evidence artifact is `live-saas-observability-43966857836`. It confirms:

- **Release mismatch:** `/api/health/live/` and `/api/health/` returned HTTP 200 but reported the older release SHA. Do not alter the expected SHA or suppress this check; deploy the intended Backend revision from the correct Render-linked repository/branch and verify the runtime-reported SHA.
- **Cache unavailable:** readiness returned HTTP 503 with `cache_url_missing`. Configure `CACHE_URL` from the actual Render Key Value service's internal connection details; never invent a Redis URL or replace Redis with local-memory cache in production.
- **Runner not ready:** readiness reported `runner: false` but did not expose a safe diagnostic category in this response. Inspect the API's structured `runner_readiness_failed` event and the authenticated Runner `/capabilities` response in Render. Keep strict Bubblewrap isolation and network enforcement; do not use shared-container or compatibility fallback.
- **Other observed checks:** database, secret key, allowed hosts, email, and worker were true. Frontend root and both Web IDE routes returned HTTP 200. These do not establish API readiness or safe execution.

The incident remains open. The repository-side CI is green, including backend/frontend, strict Runner integration, Docker Compose, PostgreSQL backup/restore, CodeQL, and dependency checks; that evidence does not repair Render environment configuration or deploy a new release. Close this incident only after the API reports the intended deployed SHA, Redis and strict Runner checks pass, readiness returns HTTP 200 with all required checks true, and the live monitor passes all 13 checks.

---

## Current incident snapshot (2026-11-16)

The latest scheduled Live SaaS Smoke Monitor run is `43904045597` (2026-11-16 16:11 UTC). It completed with **9/13 checks passing and 4 failing**. The deployed API still reports release `23f09da8a3cf3cd3ba27df666e191d1417ee6bb2`, while the expected `master` release is `a6d1b612229e391744f699cabd324cd6f52122ac` (62 commits behind at the time of the probe).

The latest probe confirms two distinct production blockers:

- **Release mismatch:** the API liveness and health responses return HTTP 200 but expose the old release SHA, so both fail the release-identity contract.
- **Readiness failure:** `/api/health/ready/` returns HTTP 503. Safe diagnostics report `database: true`, `worker: true`, `cache: false`, and `runner: false`. The API security-header check also fails because its endpoint returned HTTP 503.

The frontend root and Web IDE routes return HTTP 200. That does not establish API readiness or safe code execution. Cache and strict isolated Runner remain separate dependencies; fixing one does not imply the other is healthy.

This is a live Render incident, not a PR-CI failure. Pull-request executions of the monitor run contract tests only and explicitly skip external production probes. A green pull-request monitor job is not evidence of production health. The incident remains open until the API release identity matches the intended deployed commit, readiness returns HTTP 200 with every mandatory check true, and the live monitor passes all 13 checks.

Earlier 2026-11-16 snapshot (run `43815825025`): the same deployed release and cache/Runner readiness failures were observed. The later run `43904045597` confirms they remain unresolved.

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

### Runner readiness diagnostics

The API's Runner readiness probe now records a structured, secret-safe `runner_readiness_failed` event and returns a bounded diagnostic category alongside the existing `checks.runner` boolean. It never logs the Runner URL, bearer token, response body, or exception message.

| Diagnostic | Meaning | Corrective action |
|---|---|---|
| `runner_url_missing` | `IDE_RUNNER_URL` is not configured | Set it to the dedicated Runner's private/internal service URL, reachable from the API service |
| `runner_token_missing` | The API has no `IDE_RUNNER_TOKEN` | Set a high-entropy token on both API and Runner; compare by securely rotating both values, never by pasting them into logs |
| `runner_request_error:Timeout` / `ConnectionError` | The API cannot reach the configured Runner endpoint | Verify service health, private networking, hostname/port, and service restart state |
| `runner_http_status:401` | Runner rejected the control-plane credential | Rotate the same token on both services and redeploy both |
| `runner_http_status:503` | Runner is reachable but its capabilities endpoint is unavailable | Inspect Runner startup/runtime logs and its own `/health` endpoint |
| `runner_capability_contract_mismatch` | Runner responds, but does not advertise the required strict isolated execution contract | Inspect authenticated `/capabilities`; require strict Bubblewrap, execution enabled, no shared container, isolated network enforcement, and execute/process operations enabled |
| `runner_invalid_response` | Runner returned a non-JSON or malformed capabilities response | Verify the API points to the Runner service, not a proxy, frontend, or unrelated endpoint |

A `runner_capability_contract_mismatch` caused by blocked Linux namespaces is a host-runtime limitation. Keep execution blocked and move Runner to a compatible isolated Linux runtime. Do not switch to compatibility/shared-container mode, enable network egress, or weaken the capability checks.

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
