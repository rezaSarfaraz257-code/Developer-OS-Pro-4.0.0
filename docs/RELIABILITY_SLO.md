# Developer OS Reliability Contract

This document defines the operational targets for the production layer. It is a policy contract, not a claim that the current deployment already meets every target.

## Service-level objectives

| Signal | Target | Window | Measurement intent |
|---|---:|---|---|
| API availability | 99.95% | rolling 28 days | Successful customer-facing API requests |
| Readiness availability | 99.95% | rolling 28 days | `/api/health/ready/` available when the service should accept traffic |
| API latency | p95 < 500 ms | rolling 28 days | Excluding long-running async/streaming execution endpoints |
| API latency | p99 < 1.5 s | rolling 28 days | Same scope as p95 |
| Recovery point objective | <= 15 min | continuous | Maximum acceptable data-loss interval |
| Recovery time objective | <= 60 min | per P1 incident | Time to restore critical service |
| Backup restore verification | 100% | scheduled | Every production backup policy must have a tested restore path |

## Error-budget policy

The 99.95% availability objective leaves a 0.05% monthly/rolling-window error budget. Releases continue while the budget is healthy. When the budget is exhausted, non-emergency production changes should pause until reliability is restored; security fixes and changes that directly reduce the incident continue.

Burn-rate alerts should be based on both a fast window and a long window so a short severe outage and a slow reliability regression are detected. The exact thresholds must be calibrated against real traffic before they page an operator.

This follows the SRE model where an error budget is the complement of the SLO and is used to balance release velocity with reliability. See the Google SRE guidance: https://sre.google/workbook/error-budget-policy/

## Health semantics

- **Liveness**: `/api/health/live/` checks only that the process can answer HTTP. It must not depend on PostgreSQL, Redis, email, GitHub, AI providers, or other remote systems.
- **Readiness**: `/api/health/ready/` checks the dependencies required for this replica to receive normal traffic.
- **Application health**: `/api/health/` provides a broader basic health signal.

Liveness and readiness intentionally remain separate. A dependency outage should normally remove a replica from traffic rather than trigger an unnecessary process restart.

## Incident rules

1. P0/P1 incidents require a timeline, impact statement, root cause, mitigation, and follow-up actions.
2. If a single incident consumes a material fraction of the error budget, create a reliability action item before normal feature velocity resumes.
3. Never mark a backup as recoverable until a restore has actually been tested.
4. Do not page on every dependency failure; page on customer-impacting symptoms or sustained burn rate.
5. Correlation IDs, release version, request latency and dependency health should be available for incident diagnosis.

## Production principle

Operational controls should fail closed for unsafe configuration, but fail gracefully for optional dependencies. AI, GitHub and other non-core integrations should degrade independently rather than making the entire Developer OS unavailable.
