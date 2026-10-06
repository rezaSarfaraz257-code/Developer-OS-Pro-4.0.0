# Developer OS — Production Runtime Contract

The existing application and PostgreSQL data model remain the source of truth. IDE runner workspaces are disposable caches: the API persists workspace files and synchronizes them into the runner before execution. A runner restart must therefore never be treated as deletion of user code.

## Render topology

The Blueprint explicitly defines API, runner, frontend, worker, Key Value and PostgreSQL. The frontend no longer contains a hard-coded backend hostname; Nginx receives BACKEND_ORIGIN at runtime.

Render services have ephemeral filesystems by default. Durable application data must live in PostgreSQL, Key Value, object storage, or an attached persistent disk. Runner-local files are never treated as durable storage.

## Runner isolation

Container-native execution is defense-in-depth. It is not equivalent to a dedicated microVM sandbox with enforceable network isolation. The repository keeps Bubblewrap as an optional backend for infrastructure that supports the required Linux namespaces. For hostile arbitrary-code execution at internet scale, the production target is a dedicated sandbox boundary such as Firecracker, Kata, gVisor, or an equivalent isolated execution service.

## SaaS tiers

The existing entitlement system remains authoritative. Frontend visibility is not authorization. API endpoints enforce plan limits server-side, and verified billing webhooks remain authoritative for paid subscription state. Free, Pro, Team and Enterprise can therefore use different limits and capabilities without duplicating authorization logic in React.

## Release gates

1. Django deploy checks pass.
2. makemigrations check passes.
3. PostgreSQL migrations apply from zero and against the current schema.
4. Frontend lint, build and tests pass.
5. Runner unit and container smoke tests pass.
6. Full Docker E2E passes.
7. Render Blueprint is validated before infrastructure sync.
8. Production is not declared complete until external billing, email, AI credentials and runner isolation are configured for the target environment.
