# Developer OS — 100% Production Contract

This release is considered **production-validated** only when the target CI/host has produced passing results for every gate below. The repository does not claim a successful external run from static inspection alone.

## Required gates

1. Python compile and Django `check --deploy`.
2. PostgreSQL migration from an empty database and a current production-like database.
3. Full Django test suite.
4. Frontend `npm ci`, lint, build and tests.
5. Runner unit tests.
6. Runner Docker build, health check and authenticated execution integration test.
7. Complete Docker stack with PostgreSQL, backend, runner and frontend.
8. End-to-end registration → login → project → task → workspace → AI → usage flow.
9. Stripe test-mode checkout/webhook idempotency and subscription state transitions.
10. Production HTTPS/CORS/CSRF validation against the exact deployed frontend origin.
11. Backup and restore test for PostgreSQL/media.
12. Release artifact gate with no local database, node_modules or cache artifacts.

## IDE

The product IDE is designed around a persistent project filesystem, isolated execution runner, package installation, framework presets, terminal, file operations, project binding and keyboard shortcuts. The current frontend already uses a pinned `monaco-editor` package and dedicated Monaco editor/diff components. The production gate is therefore to validate the connected Monaco implementation in CI and in the live IDE flow; this contract must not claim that Monaco is still an unpinned CDN dependency.

IDE execution remains intentionally isolated from the Django web process. Production validation must verify that Run/terminal execution is handled by the sandboxed runner boundary with the required resource, filesystem and network restrictions.

## Important

“100%” here means all defined release gates have passed; it does not mean software can mathematically contain zero bugs. Production monitoring, rollback and incident response remain part of normal SaaS operation.
