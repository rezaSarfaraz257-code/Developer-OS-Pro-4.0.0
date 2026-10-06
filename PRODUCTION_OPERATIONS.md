# Developer OS — Production Operations

## Release gate
A release is production-ready only after CI passes on the exact release commit and the target deployment passes HTTPS/readiness/IDE/billing checks.

## Deployment
1. Run production preflight and Django deploy checks.
2. Apply migrations and verify `/api/health/ready/`.
3. Verify authenticated IDE file CRUD → execute → process → debugger.
4. Verify Stripe test-mode checkout and signed webhook delivery before live billing.
5. Verify AI success, timeout, quota exhaustion and provider-unavailable fallback.
6. Verify error/latency/runner/webhook/backup alerts.

## Observability
Use structured logs plus OpenTelemetry-compatible traces and an error tracker such as Sentry. Alert on sustained 5xx, readiness failures, runner queue saturation, execution failures, webhook failures and backup failures. Runner exposes authenticated metrics/diagnostics; backend exposes health/readiness and operational metrics.

## Recovery
Use encrypted off-site PostgreSQL custom-format backups and provider-native object-storage versioning/retention for media. A backup is verified only after destructive restore succeeds. Target RPO ≤24h and RTO ≤4h; keep at least 7 daily and 4 weekly copies and test restores monthly and after major schema changes.

## Runner security
For hostile arbitrary-code multi-tenancy, move execution to dedicated isolated workers using Firecracker, gVisor, Kata Containers or equivalent, with explicit egress policy. The Docker runner is a defense-in-depth boundary, not a kernel-level security guarantee.

## Incident response
Record release SHA, failing gate, logs, correlation ID and affected tenant/workspace. Roll back the application artifact first when safe; restore data only for confirmed corruption.

## 100% definition
100% means every defined release gate has a passing recorded result on the target environment; it does not mean zero future defects.
