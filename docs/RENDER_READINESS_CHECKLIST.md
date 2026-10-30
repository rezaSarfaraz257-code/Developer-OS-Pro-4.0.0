# Render production readiness checklist

Use this checklist when the public API responds to `/api/health/` but `/api/health/ready/` returns HTTP 503. Liveness means the process responds; readiness intentionally requires production dependencies and configuration.

## Triage when liveness and readiness both return 503

If `/api/health/live/` and `/api/health/ready/` both return HTTP 503 (or the host returns a platform-generated 503 before JSON is returned), treat this as a **service availability/startup incident**, not merely a failed readiness check. The readiness `checks` object is only diagnostic evidence if the application itself returned its JSON response.

1. Open the Render API service and check service events/deploy history first. Confirm it is not suspended, deploying, repeatedly restarting, or failing its configured health check.
2. Inspect logs around the latest deploy for the first startup/import/configuration/database exception and the process exit reason. Capture only the exception type, safe message, timestamp, and deployed commit; redact secrets and personal data.
3. Verify the configured Render health-check path is the lightweight `/api/health/live/` endpoint, not a dependency-heavy readiness endpoint. Liveness must not require the database, Redis, SMTP, or worker.
4. Confirm the deployed `RENDER_GIT_COMMIT`/release identity and compare it with the intended branch or commit. A green CI run for an undeployed commit cannot prove the live service is healthy.
5. Once the application responds, inspect the JSON from `/api/health/ready/?format=json` and fix the specific false checks. If the origin still returns platform-level 503, use service logs/events to identify the startup or routing failure; do not infer which readiness dependency failed.
6. Recheck all health endpoints after a successful restart/deploy. Do not silence health checks, loosen security settings, or report success based on the frontend loading alone.

## Required web-service environment

Configure values in the Render API web service's Environment page. Never commit credentials to this repository or paste them into public issues.

- `ENVIRONMENT=production`
- `DEBUG=false`
- `DJANGO_SECRET_KEY`: a unique, high-entropy production secret.
- `DATABASE_URL`: the intended PostgreSQL connection URL. For Neon, use the correct project/branch and the provider's recommended SSL connection string. Do not use a URL from an expired Render database.
- `ALLOWED_HOSTS=dos-o91y.onrender.com` plus any other actual API custom domain hostnames, comma-separated. Hostnames only; no scheme or path.
- `FRONTEND_URL=https://developer-os-version-3-0-5.onrender.com` (or the actual deployed frontend URL).
- `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS`: include only the exact trusted frontend origin(s), with `https://` schemes. Keep these aligned with the actual deployed frontend.
- SMTP settings: `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, and provider-appropriate `EMAIL_USE_TLS` / `EMAIL_USE_SSL`. Do not use both TLS and SSL simultaneously. The sender must be authorized by the provider.
- `CACHE_URL`: a reachable Redis URL supported by the configured Django Redis cache backend, with TLS/authentication as required by the provider.

Keep the bounded database pool defaults unless the database provider's connection limits and the total number of web/worker processes have been reviewed. Never solve readiness by disabling TLS, weakening host validation, switching production email to the console backend, or using an unsafe runner fallback.

## Runner isolation (production release gate)

- Keep `RUNNER_SECURITY_LEVEL=strict` on deployed runners. The Runner now forces strict mode when `ENVIRONMENT=production|prod`, `RENDER=true`, or Render deployment markers are present, even if `RUNNER_SECURITY_LEVEL=compat` was mistakenly configured.
- `compat` is only for trusted local/CI execution. It does not provide per-execution tenant isolation and must never be used as a production workaround.
- With strict mode and `RUNNER_SANDBOX_MODE=container`, execution intentionally returns HTTP 503 because a shared service container is not a per-execution security boundary.
- Set `RUNNER_SANDBOX_MODE=bwrap` only on a host where Bubblewrap can create the required namespaces and the isolation checks pass in that actual runtime. Do not disable strict mode or skip the checks to make execution appear available.
- If the host denies the required namespace/capability operations, keep execution blocked and deploy the Runner to an isolated runtime that supports per-execution sandboxing.
- Before production approval, execute a real test program as an untrusted tenant and verify output/exit status, timeout and cancellation cleanup, quota enforcement, and that another tenant cannot read files, environment values, process output, or secrets.

## Background worker

The default Render deployment starts the durable worker in the API container when Render runtime variables are present. Keep `BACKGROUND_WORKER_MODE` unset or set to `embedded` unless a separate worker is actually deployed and healthy.

If a separate worker is intentionally deployed, set `BACKGROUND_WORKER_MODE=external` on the API service only after confirming the worker uses the same release and `DATABASE_URL`. Do not set `disabled` in production. The worker must create a fresh `background-worker` heartbeat; a stale heartbeat or stale running job makes readiness fail.

Check Render logs for API startup and worker startup/restart messages. Do not expose database URLs, SMTP passwords, verification links, access tokens, or environment dumps in logs or support messages.

## Verify after applying environment changes

1. Save the Render environment changes and wait for the service to redeploy/restart.
2. Check `https://dos-o91y.onrender.com/api/health/live/` — expected HTTP 200 and `status: alive`.
3. Check `https://dos-o91y.onrender.com/api/health/` — expected HTTP 200 and `database: ok`.
4. Check `https://dos-o91y.onrender.com/api/health/ready/?format=json` — expected HTTP 200 and `status: ready`, with every mandatory check true.
5. If readiness remains 503, inspect the returned non-secret `checks` object and correlate it with server logs. Fix the named configuration/dependency; do not bypass the check or make the endpoint return a false green status.
6. Verify a real test email reaches a mailbox you control and that a queued background job transitions to a terminal success state.
7. Confirm the reported `release_sha` matches the intended deployed commit before treating a code change as live.

The public `/api/status/` endpoint reports incidents and is not a substitute for readiness. A passing CI run validates the tested commit and CI environment; it does not prove Render's runtime environment is configured correctly.


## Readiness failure decision matrix

The API's readiness view deliberately returns HTTP 503 when required checks fail. Use the JSON `checks` field to identify the failed names; if the platform returns a non-JSON 503, use Render service events and application logs instead.

| Failed check | Verify first | Safe remediation |
| --- | --- | --- |
| `database` | The database service is reachable; `DATABASE_URL` points to the intended PostgreSQL instance; the configured default Django engine is PostgreSQL in production. | Correct the service URL/credentials or database availability. Keep SSL verification and the production database requirement enabled. |
| `secret_key` | `DJANGO_SECRET_KEY` is present and is not Django's insecure development default. | Generate a unique high-entropy secret in the hosting dashboard. Do not commit it or print it in logs. |
| `allowed_hosts` | `ALLOWED_HOSTS` contains the API hostname and has no wildcard; production entries are not loopback-only. | Add only actual API hostnames. Do not use `*` to make the check pass. |
| `email` | `EMAIL_HOST` and a real mail backend are configured; production is not using Django's console email backend. | Configure an authorized SMTP provider and verify delivery with a controlled test mailbox. Never paste SMTP credentials into logs or issues. |
| `cache` | Redis is reachable; the Django default cache backend is Redis in production; `CACHE_URL` is present and matches that backend. | Correct the Redis URL/TLS/authentication and restart the service. Do not substitute local-memory cache in production just to pass readiness. |
| `worker` | A `background-worker` heartbeat was written within 90 seconds; no `running` job has a stale/missing lock. | Verify the embedded worker is starting, or configure `BACKGROUND_WORKER_MODE=external` only when a separate healthy worker uses the same release and database. Inspect safe worker logs and stale jobs before restarting/recovering work. |

Important implementation detail: the worker check becomes mandatory in production/Render runtime, while the database, secret, host, email, and cache checks are part of the production readiness gate. Do not infer which check failed from the HTTP status alone.

## Evidence required before marking the release ready

Record these non-secret facts in the PR or release notes:

- The full CI run ID and green result for runner, backend, frontend, PostgreSQL restore, Docker Compose, and static operational gates.
- The deployed release SHA and the intended commit SHA.
- The live liveness, database health, and readiness HTTP statuses; include only allow-listed check names, never raw environment values.
- A real isolated-runner test result covering execution output/exit status, timeout/cancellation cleanup, quotas, and cross-tenant isolation.

If the last two items are not available, the branch can be CI-green but must not be described as fully production-verified.
