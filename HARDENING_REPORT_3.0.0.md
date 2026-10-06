# Developer OS 4.0.0 — Mature SaaS Hardening Report

## Scope
This release pass targets the gaps found during the mature-SaaS review: runner path traversal, API-key authentication wiring, workspace isolation, database connection resilience, and release validation.

## Implemented
- API keys are now registered in DRF's authentication chain before JWT authentication.
- API-key authentication uses constant-time token comparison and updates `last_used_at`.
- Runner rejects absolute paths, Windows drive paths, traversal segments, dot segments, empty path components, and `.git` paths before normalization.
- Runner rejects symlink-based workspace escapes before writing files.
- Runner snapshots exclude dependency/cache directories and symlinks and enforce the source-size ceiling while collecting output.
- Runner authentication uses constant-time comparison.
- PostgreSQL uses bounded persistent connections with health checks (`DB_CONN_MAX_AGE`, default 60 seconds).
- Production security headers include COOP/CORP in addition to the existing HSTS, X-Frame-Options and nosniff controls.
- Added regression tests for valid and invalid API-key authentication.

## Verified in this release environment
- Python compilation: PASS
- Static release gate: PASS
- Runner security tests: 4/4 PASS

## Environment-limited checks
The execution environment has no working package-registry/DNS access and no Docker daemon. Therefore the following cannot be truthfully marked as executed here:
- Full Django test suite
- `manage.py check --deploy`
- migration drift check
- frontend `npm ci`, lint, build and test
- Docker image build and compose health validation
- live PostgreSQL/Stripe/GitHub/AI end-to-end validation

These checks are enforced by `.github/workflows/ci.yml` and must pass in the target CI/hosting environment before declaring an externally validated production release.
