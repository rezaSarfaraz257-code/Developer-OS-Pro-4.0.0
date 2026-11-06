# Developer OS — Baseline Audit (2026-11-07)

**Scope:** Repository source and GitHub branch/CI metadata available during this audit.
**Purpose:** Establish a truthful starting point for the Autonomous Software Universe roadmap.
**Limit:** This is not a live penetration test, full source audit, or production acceptance certificate.

## Executive assessment

Developer OS already has a substantial product foundation. The next step is not a rewrite. It is to make the existing workspace dependable, prove secure execution end to end, and then build trusted AI task execution and orchestration on top.

The most important near-term technical gate is the secure Runner readiness contract. An existing isolated PR (#211) addresses a concrete mismatch between core execution readiness and optional package-install/preview bridges. Its observed CI workflows all completed successfully in the latest retrieved run set. It remains unmerged and must still receive human review and live deployment verification.

## Evidence-backed capability inventory

| Area | Evidence in current repository | Assessment |
|---|---|---|
| Product foundation | README documents React/Vite frontend, Django REST backend, PostgreSQL, JWT, Docker/Nginx/Gunicorn | Implemented foundation; production acceptance is separate |
| Workspace and project management | README documents projects, tasks, notes, snippets, favorites, collaboration, activity and analytics | Implemented product surface; each journey needs current E2E evidence |
| Web IDE | README documents persistent multi-file browser workspace, file tree, editor and terminal surface | Partial/needs end-to-end verification |
| Independent repository | README documents working tree, immutable SHA-256 snapshots, commits, branches, push/pull, clone and history | Documented implementation; concurrency, recovery and restore need acceptance tests |
| AI assistant | README documents optional OpenAI-compatible provider; current frontend includes workspace-context assistant integration | Partial; provider configuration, tool execution, patch safety, and task completion require live evidence |
| Language tooling | Current Monaco editor imports LSP request/response mapping and document queue modules; Runner contains an LSP engine | Partial; language coverage, cancellation, stale-response handling and multi-file correctness need systematic tests |
| Secure code execution | README states arbitrary user code is not run inside Django; execution crosses an isolated Runner/worker boundary | Architecture boundary exists; live sandbox isolation and lifecycle must be proven |
| SaaS / billing | README documents subscriptions, entitlements, organizations and API-key foundations; release gate requires webhook signature/idempotency checks | Partial; real provider credentials, webhook tests, metering and reconciliation remain acceptance gates |
| Production operations | Health/readiness endpoints, release identity and CI workflows are present | Partial; live service health, cache/worker/runner state and recovery need continuous evidence |

## Confirmed high-priority findings

### 1. Runner readiness contract mismatch on current master

The current master implementation of _secure_runner_ready() requires install and preview operations and a preview network bridge as part of core readiness. That couples the core execute/process service to optional capabilities that may intentionally remain unavailable under a strict isolated configuration.

PR #211 (fix/strict-runner-readiness-contract-20261106) changes the core readiness contract to require:
- strict Bubblewrap sandbox (bwrap / bubblewrap);
- strict security level and execution availability;
- no trusted shared container;
- isolated network enforcement;
- working execute and process operations.

It deliberately leaves package-install and preview unavailable unless their own secure bridges are deployed, preserving fail-closed endpoint guards.

**Observed evidence:** PR #211 is 2 commits ahead of master, 0 behind at audit time. The retrieved workflow runs for its head commit report success for Dependency Review, Operational Policy Gate, Live SaaS Smoke Monitor, Security, Backend Health Contract Tests, Node Compatibility, Container Security, and Production Validation.

**Disposition:** Strong first merge candidate after human code review and confirmation that optional install/preview endpoints still return their intended safe unavailable responses. Do not treat green CI as proof that the deployed Runner is actually provisioned.

### 2. Email transport PR requires branch synchronization

PR #209 adds a Resend HTTPS backend for environments where outbound SMTP is unavailable. Its retrieved workflow runs all report success. However, its branch is 12 commits ahead and 1 commit behind current master.

**Disposition:** Do not merge blindly. Rebase/synchronize with current master, rerun all checks, and validate sender-domain configuration and delivery from the actual deployment environment. No credentials should be committed.

### 3. Web IDE modernization branch is not merge-ready

The branch feat/webide-workbench-rebased-20261105 contains a substantial Monaco/LSP/editor update, including editor changes, workbench CSS, completion mapping, LSP document queue, explorer conflict helper, and tests. The comparison against current master reports 1 commit ahead and 9 behind.

**Disposition:** Do not merge the branch as-is. Review each change against current master, transplant only compatible improvements onto a fresh branch, and run the full frontend and Runner test suites. Avoid overwriting newer IDE fixes.

### 4. IDE maintainability needs incremental improvement

Frontend/src/ProIDE.jsx currently concentrates a large amount of editor/workspace behavior in one component and suppresses several lint rule families at file scope.

**Disposition:** Do not rewrite the IDE. First add/retain behavior tests around workspace loading, saving, conflict resolution, offline synchronization, terminal lifecycle, and editor state. Then extract bounded hooks/modules one responsibility at a time with no UI or API behavior change.

## Current open pull requests observed

| PR | Scope | Snapshot |
|---|---|---|
| #212 | Autonomous Software Universe roadmap | Draft documentation PR; 4 commits ahead, 0 behind at audit time |
| #211 | Strict Runner readiness contract | 2 commits ahead, 0 behind; all retrieved CI workflows successful |
| #209 | Resend HTTPS email delivery | 12 commits ahead, 1 behind; all retrieved CI workflows successful, but requires synchronization |

Branch and workflow state can change after this audit. Recheck GitHub before any merge or release decision.

## Recommended execution order

1. Review and land the isolated Runner readiness correction only after human approval; verify live readiness and optional bridge fail-closed behavior.
2. Synchronize and revalidate the email transport change if it is still needed for the active deployment.
3. Stabilize Web IDE persistence: server-authoritative saves, explicit save state, stale-write/conflict handling, offline replay, and recovery.
4. Prove secure Runner job lifecycle: authenticated submission, isolation, quotas, cancellation, logs, artifacts, and tenant separation.
5. Harden AI as a bounded project-aware engineering assistant with reviewable diffs, tool authorization, budgets, and test evidence.
6. Introduce multi-agent orchestration only after single-agent tasks are reliable and idempotent.
7. Build the evidence-backed Project Digital Twin and end-to-end deploy/monitor/rollback loop.

## Release discipline

- Keep master protected; use isolated branches and reviewable pull requests.
- Never bypass sandbox checks or disable readiness gates to obtain a green deployment.
- Never report a feature as complete from UI presence, documentation, or a successful build alone.
- For every milestone, retain test results, deployment identity, operational evidence, and a rollback path.
- Treat this audit as a dated snapshot, not a permanent source of truth.

## Verified follow-up — 2026-11-07

This section supersedes any branch-count or PR-state snapshot above where the two conflict. It was checked against GitHub after the original audit text was drafted.

### Current PR #212 state

- PR #212 remains an open draft, based on `master` at `daed3bf4c8d843c2b568c4e154a22dfbdd5764cf`.
- Its roadmap branch is 7 commits ahead and 0 behind `master`; it changes five documentation files only.
- It does not add runtime orchestration, persistent agent runs, task DAGs, agent permissions, approval workflows, or new AI execution capabilities.

### Web IDE collaboration work is a separate engineering stream

PR #213 (`fix/webide-serialized-collab-patches-20261107`) is open and draft, 75 commits ahead and 0 behind `master` at head `aa6b8d884e9596f8ebc3eebe7e2d89904877ca7a`. It changes ten backend/frontend files and adds revision-ordered collaboration, offline recovery, explicit conflict review, and CRDT validation. Its PR description reports seven successful CI workflows, including backend/frontend/Runner validation, CodeQL, dependency checks, container security, PostgreSQL backup/restore, and Docker Compose health validation. Manual two-authenticated-browser collaboration E2E remains outstanding; PR-triggered checks do not certify live production availability. This is meaningful Phase 1 work, but it is not an AI-agent orchestrator.

### Existing AI foundation — implementation boundary

The current backend contains `AIConversation` and `AIMessage` persistence models, an authenticated `/api/assistant/` endpoint, and structured `ai_actions_api` actions for review, tests, explain, plan, and debug. The assistant endpoint builds deterministic project/task summary context and can call an optional OpenAI-compatible provider. The structured action endpoint enforces an AI usage allowance and project/workspace access checks; its local fallback is deliberately advisory.

These are useful foundations, but source inspection does not establish an autonomous engineering system:
- The inspected `assistant_api` returns a response but does not persist a conversation/message in the shown handler.
- The structured actions do not create durable agent-run records, task dependency graphs, resumable execution state, or a specialist-agent registry.
- No verified tool-execution contract, patch-application transaction, approval queue, cancellation lifecycle, or per-agent permission boundary was found in the inspected routes/handlers.
- The current assistant must therefore be classified as **partial AI copilot**, not AI Agent Build or multi-agent orchestration.

### Corrected execution order for the Autonomous Software Universe

1. Complete Phase 1 workspace reliability and manual multi-client E2E evidence (including review of PR #213); do not treat PR #213 as an agent feature.
2. Prove Phase 2 Runner isolation, quotas, cancellation, artifact boundaries, and fail-closed behavior in the deployed environment. PR #211 remains subject to human review and live verification.
3. Build Phase 3 as a bounded, durable single-agent workflow: persisted run/task state, authorized project context, structured plan, reviewable patch proposal, test evidence, approval, apply/rollback, cancellation, and cost/time limits. Reuse existing AI models and usage accounting only after checking their constraints; avoid duplicate sources of truth.
4. Add Phase 4 specialist agents and orchestration only after the single-agent workflow is repeatable and idempotent.
5. Keep deploy/monitor/rollback and the Project Digital Twin behind verified runner and lifecycle evidence.

**Implementation gate:** Before adding models or migrations, inspect the complete existing AI, workspace, usage, audit, job, and runner schemas and all corresponding authorization paths. Every agent-run operation must be tenant-scoped, idempotent, auditable, cancellable, resource-bounded, and unable to execute untrusted code outside the strict Runner. No agent may push, deploy, spend money, access secrets, or perform destructive data operations without explicit authorization and approval.


## Implementation hardening — 2026-11-07

The roadmap branch now includes a narrowly scoped hardening pass on the existing AI patch-approval path (not a new parallel agent subsystem):

- AI-generated patch paths are checked against the IDE workspace contract: reject absolute and drive-prefixed paths, backslashes, empty/dot/traversal components, reserved runtime roots (`.git`, `node_modules`, virtual environments and caches), invalid path characters, duplicate case-insensitive targets, and individual file contents above 1 MB.
- Approved patch application now locks the workspace row inside a database transaction before checking the signed approval's workspace revision. It re-checks write permission under the lock and persists the patch only against the exact revision that was approved, preventing concurrent approvals from silently overwriting one another.
- Replaying an approval after the workspace revision changes is rejected as stale. A Runner synchronization failure is reported as `applied_sync_pending`, not mislabeled as a fully synchronized apply.
- Regression coverage was added for reserved/ambiguous paths, duplicate case-insensitive targets, oversized files, cross-user approval-token use, and approval replay after a successful apply.

**Commits:** `0c99c8c` (patch path contract), `0451d57` (serialized approval apply), `f39ba80` (regression tests), and `c1369a2` (fixture correction).

**Verification status at this snapshot:** GitHub reports Operational Policy Gate, Node Compatibility, Dependency Review, and Live SaaS Smoke Monitor successful; Security is queued; Container Security and Production Validation are still in progress. No local Django test run or live AI-provider/Runner acceptance test has been performed in this session. Do not mark the Autonomous Software Universe, multi-agent orchestration, or end-to-end autonomous build as complete.
