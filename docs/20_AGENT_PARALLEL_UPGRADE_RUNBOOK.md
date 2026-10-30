# Developer OS — 20-Agent Parallel Upgrade Runbook

## Objective
Run a controlled, repository-wide engineering upgrade using 20 specialist Codex agents where the active Codex runtime supports agent spawning. Each agent must work in an isolated worktree/branch, return evidence, implement only assigned fixes, and provide tests. The integration target is `master`; this campaign branch is `codex/20-agent-parallel-upgrade`.

## Truthfulness and runtime constraint
This file is a runbook, not proof that 20 agents have been launched. The current repository connector can create branches/files/PRs but cannot itself start Codex agent sessions. The operator must open this repository in a Codex environment that supports multi-agent execution and explicitly launch the task roster below. Run the maximum actually supported concurrency; do not claim 20 active agents unless the runtime confirms it. Never bypass plan or usage limits.

## Rules for every agent
- First inspect instructions, relevant code, tests, and current baseline SHA.
- Work only in a separate Git worktree/branch. Do not edit `master`.
- Respect the file ownership map. Do not modify another agent's assigned files.
- Report evidence (file and line), reproduction steps, root cause, changed files, exact test commands and exit codes.
- Make minimal, backwards-compatible changes. Preserve existing working features.
- Add regression tests for confirmed defects.
- Do not run destructive commands or use production secrets/data.
- Never commit secrets, tokens, credentials, or private user data.
- Do not claim tests passed unless they were actually run.
- Each agent submits a branch/PR and does not merge. A coordinator resolves conflicts and integrates only after review.
- If a finding requires overlapping files, report a dependency and wait for coordinator assignment instead of racing another agent.

## 20-agent task roster

### Investigation and implementation slots
1. **Coordinator / baseline (read-only until task plan approved)** — record master SHA, repository instructions, workflow state, test commands, dependency map; maintain task registry and file ownership; do not perform broad refactors.
2. **Auth/JWT** — inspect token obtain/refresh, serializer validation, auth error responses, and token flow tests. Own only authentication-specific backend files/tests assigned after mapping.
3. **PostgreSQL / Neon / pooling** — inspect database URL parsing, SSL, pooling, transaction and server-side cursor compatibility; reproduce login failures using isolated test config; own DB config/tests only.
4. **Django permissions and staff accounts** — verify user/staff/superuser semantics, endpoint permissions, and object ownership; add negative permission tests.
5. **Django API contracts** — audit endpoint/serializer/request-response consistency and add focused API regression tests; avoid auth and database configuration files.
6. **Backend domain models** — inspect project/task/note/snippet/resource/favorite/workflow data integrity and migration dependencies; propose migrations to coordinator before editing shared schema.
7. **Frontend build/tooling** — run actual package scripts, identify build/type/lint failures, and fix isolated tooling defects without broad reformatting.
8. **Frontend API/auth integration** — inspect base URL, headers, JWT refresh, error handling, loading and retry states; own API client/auth integration files only.
9. **Frontend runtime errors** — inspect undefined identifiers, React errors, console warnings, and state lifecycle; add regression tests for reproducible issues.
10. **WebIDE workspace/files** — audit workspace/file CRUD, path validation, save/rename/delete/refresh flows; coordinate backend API contracts before shared changes.
11. **WebIDE editor/layout** — audit tabs, dirty state, focus/full-screen, panels, scrolling, and terminal docking; frontend-only files assigned to this slot.
12. **IDE terminal/process integration** — inspect execute/process/stop lifecycle, error/output handling, cancellation, and process cleanup; avoid modifying runner security boundary without slot 14 coordination.
13. **Runner sandbox/security** — audit sandbox isolation, namespace support, filesystem boundaries, secrets, network egress, quotas and resource limits; no weakening sandbox to make hosted deployment appear to work.
14. **Runner reliability/observability** — inspect health, timeouts, orphan cleanup, logs/redaction, backpressure, and failure classification; coordinate shared runner files with slot 13.
15. **Billing/plan entitlements** — audit Free/Pro/Team/Enterprise enforcement, webhook verification/idempotency, seat counts and test-mode flows; never use live transactions.
16. **AI features/quotas** — inspect provider configuration, missing-key behavior, rate limits, usage quotas and safe error messages; no secrets in logs or reports.
17. **Community/referral/profile** — inspect posts/comments/likes, profile settings, referral eligibility and abuse prevention; test tenant isolation and ownership.
18. **CI/security/dependencies** — inspect GitHub Actions, dependency pinning, CodeQL/SAST/secret/container scans; avoid changing workflow semantics without explaining why.
19. **Deployment/operations** — inspect Docker/Render start commands, health checks, static/media routing, environment documentation, backup/rollback assumptions; no production mutations.
20. **Independent QA/release review** — after other PRs are available, review diffs for regressions, unintended deletions, test quality and migration safety; rerun relevant checks on the integrated candidate and publish go/no-go. Do not approve a change solely because the author says it works.

## Mandatory current investigation leads
Treat these as leads, not predetermined diagnoses:
- `POST /api/token/` returning HTTP 400 after switching the database to an external PostgreSQL/pooling provider.
- Django admin message indicating an incorrect staff username/password.
- IDE workspace file CRUD, execute/process/terminal/debug integration failures.
- Runner sandbox errors when Linux namespaces are unavailable in the hosting runtime.
- Billing/webhook correctness, AI quota enforcement, deployment health and E2E coverage.

## Coordinator integration process
1. Freeze and record baseline SHA and existing CI status.
2. Start the 20 slots only if Codex runtime confirms multi-agent support. Run independent investigations in parallel; keep shared-file implementation tasks blocked until ownership is assigned.
3. Collect all reports and deduplicate findings by evidence and severity.
4. Prioritize P0 (exploitable/data loss/outage), P1 (auth/data integrity/core workflow broken), P2 (significant functional/operational issue), P3 (minor UX/maintainability).
5. Authorize minimal fixes in isolated worktrees with regression tests.
6. Review every diff; run repository-documented tests. Record command, environment, exit code, and whether skipped/unavailable.
7. Integrate reviewed branches into a coordinator candidate branch. Resolve conflicts explicitly; never silently discard a fix.
8. Run the full supported CI/test matrix on the candidate. Verify migrations on an isolated database and use billing test mode only.
9. Open a non-draft PR targeting `master` only when the candidate is review-ready. Do not auto-merge; human approval is required.

## Final report required
- Baseline and candidate commit SHAs.
- Actual concurrent agent count and task-by-task status (complete/blocked/failed/not started).
- Findings with severity and file/line evidence.
- PR/branch links and changed-file summaries.
- Exact test commands and pass/fail/skipped results.
- Security, migration, and deployment review results.
- Remaining blockers, rollback plan, and go/no-go recommendation.
- Explicitly state whether any code was merged to `master`; do not imply merge or deployment if neither occurred.
