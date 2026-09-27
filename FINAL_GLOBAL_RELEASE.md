# Developer OS — Global SaaS Release

**Release:** 3.0.0-global  
**Status:** Production release package — not an MVP/demo branch

This package is the final hardening pass over the Web IDE release.

## User-facing capabilities

- Persistent project workspaces
- File create/edit/save/delete/rename/move, including virtual directories
- Framework presets and package installation through isolated runner containers
- npm and pip package workflows
- Python and Node runtimes in the runner image
- Git available inside workspaces for normal developer workflows
- Project-aware AI conversations and workspace context
- Collaboration, comments, notifications, organizations and role foundation
- Universal platform search
- API keys and subscription entitlement foundation
- Docker Compose production topology with PostgreSQL, Django/Gunicorn, Nginx and runner

## Final hardening included in this package

- Workspace source limits: 2,000 files / 50 MB total source / 1 MB per file
- Strict path validation and traversal prevention
- Virtual directory rename and recursive delete semantics
- Runner timeout process-group cleanup to prevent orphaned processes
- Non-root runner, dropped Linux capabilities, no-new-privileges, CPU/RAM/PID limits
- Production release gate and static Python/import validation

## Important production requirement

The runner intentionally executes developer commands in a separate service rather than inside Django. For internet-scale arbitrary-code execution, the runner should be deployed on dedicated worker nodes with a stronger isolation layer such as Firecracker/gVisor/Kata Containers, per-execution network policy, and resource quotas. The application is designed so that this isolation boundary can be replaced without changing the IDE API.

## Verification performed for this package

- Python compilation: PASS
- Repository release gate: PASS
- ZIP integrity: checked after packaging

A full browser build and Django integration test suite must still be executed by CI on the deployment environment because this packaging environment does not contain the project's complete npm/Python dependency sets.


## SaaS integrations

### Billing
Stripe is implemented as the payment authority. Set:
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_TEAM`, and `STRIPE_PRICE_ENTERPRISE`.
Paid plan selection creates a hosted Stripe Checkout session. Entitlements are updated from signed webhook events. Without these variables, paid checkout is intentionally unavailable rather than pretending that a payment succeeded.

### Developer API
Generated `dos_live_...` API keys are accepted by the REST API, hashed at rest, revocable, and tracked by last use.

### Discovery
The authenticated product now exposes an Explore surface for tools, workflows, resources, catalog search, and favorites.

### IDE isolation
The runner is separated from Django and now applies POSIX execution limits. For hostile multi-tenant arbitrary-code workloads at global scale, deploy the runner on dedicated isolated workers with a kernel-level sandbox (Firecracker, gVisor, Kata, or equivalent) and egress policy. The application boundary already isolates runner traffic from the web/API process.


## 3.0 Global SaaS Maturity Layer

This release hardens the product around real SaaS lifecycle requirements:

- Stripe webhook signature verification with event idempotency ledger.
- Subscription cancellation-at-period-end instead of unsafe local-only downgrades.
- Metered AI and IDE usage with plan-aware limits and a usage API.
- Billing portal integration.
- Organization email invitations with expiring tokens and acceptance checks.
- Organization governance audit log.
- Workspace, API-key, AI and runner execution entitlements.
- Runner command/network abuse hardening and secret-free execution environment.
- Production migration for all SaaS maturity data.

A release is considered operationally complete only after CI, migrations, build, deployment health checks and real payment-provider webhook delivery have passed in the target environment.
