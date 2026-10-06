# Developer OS 4.0.0 — Production Validation Contract

This release is engineered as a production SaaS candidate. The repository now contains a single CI contract that validates the application in layers instead of treating static inspection as production proof.

## Required validation gates

1. Django production checks
2. PostgreSQL 17 migration from an empty database
3. Django unit/integration/regression tests
4. Frontend `npm ci`, lint, build and tests
5. Runner unit tests
6. Runner container integration (`/health` + authenticated `/exec`)
7. Full Docker Compose build
8. Full Docker Compose startup with PostgreSQL, backend, runner and nginx frontend
9. Readiness probe
10. End-to-end API smoke flow: register → login → project → task → workspace → AI action → usage
11. Stripe webhook signature verification and idempotency regression
12. Static release artifact gate

## What this repository can prove locally in the review environment

- Python compile: PASS
- Runner unit tests: PASS (4/4)
- Static release gate: PASS
- Frontend dependency installation/build: not completed in this isolated environment because the npm registry was unavailable/timeout-prone.
- Docker build/runtime: not executable in this environment because Docker is unavailable.
- PostgreSQL-backed Django suite: not executable here because the required Python packages/database service are not available.

Those unavailable checks are deliberately not marked PASS. GitHub Actions is the authoritative external validation environment for them.

## AI product layer

Developer OS now exposes structured AI engineering actions:

- Review
- Generate tests
- Explain
- Plan
- Debug

With an OpenAI-compatible provider configured, these use the configured model. Without a provider, the product returns deterministic workspace-aware guidance rather than pretending a local model ran.

## Production rule

A release must not be called **production-validated** until the `Production Validation` GitHub Actions workflow completes successfully on the exact commit being deployed.

No API key, Stripe secret, GitHub OAuth secret, database password, or AI provider key is stored in this repository.
