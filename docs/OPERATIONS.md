# Developer OS — Production Operations Runbook

This runbook defines the operational baseline for a real production deployment.

## 1. Release lifecycle

1. CI must pass backend tests, frontend lint/build/tests, runner security tests, backup/restore, Compose validation and the static release gate.
2. Build the exact commit that will be deployed; do not rebuild from a mutable branch after approval.
3. Apply database migrations through the application entrypoint. PostgreSQL advisory locking serializes concurrent migration attempts during horizontally scaled startup.
4. Wait for `/api/health/ready/` to return HTTP 200 before routing traffic.
5. Run the black-box production validation workflow against the deployed immutable version.
6. Monitor error rate, latency, readiness, worker health, database health and queue/runner failures during rollout.

## 2. Health semantics

- `/api/health/` is the lightweight liveness/database probe.
- `/api/health/ready/` is the deployment readiness contract. In production it validates database connectivity, trusted hosts, non-development secret configuration, email delivery configuration and the configured cache.
- A failed readiness probe must prevent a new instance from receiving traffic.

## 3. Horizontal scaling

The API uses ASGI workers and Redis-backed shared cache/channel state in production. Do not use the in-process channel layer or local-memory cache for a multi-instance production deployment.

Keep worker count proportional to CPU and memory. Prefer explicit `WEB_CONCURRENCY` for small hosts and change it through deployment configuration rather than editing the image.

User-uploaded media should use S3-compatible persistent storage in production. Do not rely on container-local media when instances can be replaced.

## 4. Database safety

PostgreSQL is the production source of truth. Backups must be automated outside the application container and periodically restored into an isolated environment. The CI recovery job verifies that the repository's backup/restore procedure remains executable.

Before destructive schema changes:

- create and verify a fresh backup;
- deploy backward-compatible application code first when possible;
- apply the migration;
- validate readiness and critical API paths;
- keep the previous application image available for rollback.

## 5. Rollback

Application rollback and database rollback are separate operations. Prefer rolling the application image back to the previous known-good commit. Never assume a database migration can be reversed safely just because the application image can be reverted.

For an incompatible schema migration, restore from a verified backup only under an explicit incident/recovery procedure.

## 6. Runner isolation

The Web IDE API must never execute untrusted source in the Django web process. Execution is delegated to the isolated runner boundary. Production runner configuration should remain strict, disable outbound network access by default, use resource limits, drop Linux capabilities, use a read-only filesystem where supported, and keep the runner on an internal network.

A production runner is not considered safe merely because the API endpoint is authenticated; isolation is a separate security boundary.

## 7. Secrets

Keep secrets in the deployment platform's protected secret store. Never commit `.env`, API keys, webhook secrets, OAuth secrets, database passwords or runner tokens.

Rotate credentials after suspected exposure. GitHub credentials stored by the application are encrypted at rest; operational access to the encryption key must be restricted independently from application database access.

## 8. Observability

Every API response receives a validated `X-Request-ID` and `Server-Timing` value. Preserve the request ID across reverse proxies and include it when reporting incidents.

At minimum, production monitoring should alert on:

- sustained readiness failures;
- elevated HTTP 5xx rate;
- high p95/p99 API latency;
- PostgreSQL connection failures or saturation;
- Redis/cache failures;
- worker backlog/stale jobs;
- runner execution failures/timeouts;
- backup failures;
- repeated authentication/security throttling anomalies.

## 9. Release acceptance

A source-level release is not a production-certified release. Certification requires the deployed target to pass the production black-box validation workflow and the operator to confirm HTTPS, DNS, email delivery, persistent storage, backups and real-user smoke tests.
