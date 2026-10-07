# Developer OS Runner Security Boundary

The Web IDE executes user-controlled source code. The Runner is therefore a security boundary, not a normal application worker.

## Production contract

Production must run with:

- `RUNNER_SECURITY_LEVEL=strict`
- `RUNNER_SANDBOX_MODE=bwrap` (or a future stronger backend such as a dedicated microVM)
- `RUNNER_ALLOW_NETWORK=false` by default
- a container/runtime policy that does not expose host credentials, Docker sockets, or other tenants

Strict mode **fails closed** when the selected isolation backend is unavailable. It does not fall back to a shared `bash -lc` process.

## Compatibility mode

`RUNNER_SECURITY_LEVEL=compat` exists only for controlled development/CI environments where the runner container itself is the trust boundary. It must not be used for hostile multi-tenant production execution.

## Credential handling

The control-plane token is read once during Runner startup and removed from the Runner process environment before user processes are created. Child execution environments are constructed explicitly and do not inherit the control-plane token.

## Filesystem boundary

Workspace paths reject absolute paths, traversal segments, `.git` paths and symlink escapes. Workspace snapshots skip symlinks and cap file/workspace sizes.

## Why the distinction matters

A regex command blacklist is defense-in-depth only. It cannot prove that arbitrary Python/Node code cannot inspect another process, filesystem path, kernel interface or credential. Production isolation therefore comes from the execution backend and runtime policy, while the application-level controls remain secondary safeguards.
