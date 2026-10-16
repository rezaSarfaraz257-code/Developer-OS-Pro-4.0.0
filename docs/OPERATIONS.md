# Developer OS — Production Operations Runbook

This runbook defines the operational baseline for a real production deployment.

## 1. Release lifecycle

1. CI must pass backend tests, frontend lint/build/tests, runner security tests, backup/restore, Compose validation, dependency security review and the static release gates.
2. Build the exact commit that will be deployed; do not rebuild from a mutable branch after approval.
3. Apply database migrations through the application entrypoint. PostgreSQL advisory locking serializes concurrent migration attempts during horizontally scaled startup.
4. Wait for `/api/health/ready/` to return HTTP 200 before routing traffic.
5. Run the black-box production validation workflow against the deployed immutable version.
6. Monitor error rate, latency, readiness, worker health, database health and queue/runner failures during rollout.
7. If a rollout violates the error budget, stop the rollout and return traffic to the last known-good application version.

## 2. Health semantics

- `/api/health/` is the lightweight liveness/database probe.
- `/api/health/ready/` is the deployment readiness contract. In production it validates database connectivity, trusted hosts, non-development secret configuration, email delivery configuration and the configured cache.
- A failed readiness probe must prevent a new instance from receiving traffic.
- Readiness is not a substitute for synthetic user journeys; production acceptance must exercise authentication, core workspace operations, runner execution and billing boundaries.

## 3. Reliability targets and error budgets

Operational targets should be measured from real production telemetry rather than declared as source-level guarantees:

- API availability target: **99.95% rolling 28-day window**.
- Readiness availability target: **99.95% rolling 28-day window**.
- API latency target: **p95 < 500 ms** for ordinary authenticated CRUD requests, excluding long-running AI/runner jobs; **p99 < 1.5 s** for the same scope.
- API server-error budget: **aligned to the 99.95% availability SLO and measured from customer-impacting traffic**, rather than using an unrelated fixed percentage.
- Recovery point objective: **<= 15 minutes**.
- Recovery time objective: **<= 60 minutes** for a P1 incident.
- Backup recovery verification: **100% of the production backup policy** must have a tested restore path.

These are service objectives, not claims that the source code alone can guarantee them. Capacity, provider availability, database sizing, networking and external integrations must be included in the measurement.

## 4. Horizontal scaling

The API uses ASGI workers and Redis-backed shared cache/channel state in production. Do not use the in-process channel layer or local-memory cache for a multi-instance production deployment.

Keep worker count proportional to CPU and memory. Prefer explicit `WEB_CONCURRENCY` for small hosts and change it through deployment configuration rather than editing the image.

PostgreSQL connections are bounded per process through psycopg pooling. Size `DB_POOL_MAX_SIZE` together with worker count and the database connection limit; never increase both independently during a traffic event.

User-uploaded media should use S3-compatible persistent storage in production. Do not rely on container-local media when instances can be replaced.

## 5. Database safety

PostgreSQL is the production source of truth. Backups must be automated outside the application container and periodically restored into an isolated environment. The CI recovery job verifies that the repository's backup/restore procedure remains executable.

Before destructive schema changes:

- create and verify a fresh backup;
- deploy backward-compatible application code first when possible;
- apply the migration;
- validate readiness and critical API paths;
- keep the previous application image available for rollback.

## 6. Rollback

Application rollback and database rollback are separate operations. Prefer rolling the application image back to the previous known-good commit. Never assume a database migration can be reversed safely just because the application image can be reverted.

For an incompatible schema migration, restore from a verified backup only under an explicit incident/recovery procedure.

## 7. Runner isolation

The Web IDE API must never execute untrusted source in the Django web process. Execution is delegated to the isolated runner boundary. Production runner configuration should remain strict, disable outbound network access by default, use resource limits, drop Linux capabilities, use a read-only filesystem where supported, and keep the runner on an internal network.

A production runner is not considered safe merely because the API endpoint is authenticated; isolation is a separate security boundary.

## 8. Secrets and supply-chain security

Keep secrets in the deployment platform's protected secret store. Never commit `.env`, API keys, webhook secrets, OAuth secrets, database passwords or runner tokens.

Rotate credentials after suspected exposure. GitHub credentials stored by the application are encrypted at rest; operational access to the encryption key must be restricted independently from application database access.

Production dependencies are continuously reviewed through dependency auditing and Dependabot. Pull requests also run GitHub's dependency-review gate. The deployment process should promote only artifacts produced from an approved immutable commit.

Where container/image delivery is introduced, use signed provenance/SBOM attestations and verify them before production promotion. GitHub artifact attestations provide cryptographic provenance and can include SBOM information; verification is required for the attestation to provide a deployment trust boundary.

## 9. Observability

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

OpenTelemetry is intentionally an optional observability layer rather than a hard dependency of the core release gate. Python traces and metrics are currently stable, while the broader signal/component maturity is mixed; instrumentation should be enabled only with a tested exporter/backend and initialized correctly for the Gunicorn/Uvicorn worker model.

## 10. Incident response

For a production incident:

1. Identify the affected surface and preserve the first failing request ID.
2. Check readiness, error rate, latency, database pool saturation, Redis, worker backlog and runner health.
3. Stop an unsafe rollout before attempting feature-level fixes.
4. Roll back the application to the last known-good version when the failure is deployment-related.
5. Do not perform destructive database changes during a live incident unless the recovery procedure explicitly requires them.
6. Preserve logs and audit/security events needed for post-incident analysis.
7. Record the trigger, impact, mitigation, recovery time and follow-up prevention work.

## 11. Release acceptance

A source-level release is not a production-certified release. Certification requires the deployed target to pass the production black-box validation workflow and the operator to confirm HTTPS, DNS, email delivery, persistent storage, backups and real-user smoke tests.
