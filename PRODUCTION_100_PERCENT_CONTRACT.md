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

The frontend currently pins `monaco-editor@0.55.1` in `Frontend/package.json` and bundles Monaco workers locally through Vite; it does not depend on an unpinned external editor CDN. This establishes the editor dependency, but does not by itself prove VS Code parity. Browser end-to-end coverage must still validate multi-file editing, save/recovery, language services, terminal, debugger, package installation and preview workflows.

Strict Bubblewrap execution currently isolates each process in a network namespace. The Runner must not expose preview ports by disabling that isolation. Until a dedicated, policy-enforced preview network bridge is implemented, the Runner reports preview as unavailable in Bubblewrap mode and production readiness must remain false for the full IDE contract.

## Important

“100%” here means all defined release gates have passed; it does not mean software can mathematically contain zero bugs. Production monitoring, rollback and incident response remain part of normal SaaS operation.
