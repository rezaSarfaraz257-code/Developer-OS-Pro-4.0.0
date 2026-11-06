# Developer OS — Autonomous Software Universe Roadmap

**Status:** Strategic product direction and staged delivery plan  
**Applies to:** Developer OS 4.0.0  
**Decision:** Evolve the existing platform incrementally. Do not replace its working foundations.

## Baseline audit

The dated, evidence-backed starting assessment is maintained in [Baseline Audit — 2026-11-07](BASELINE_AUDIT_2026-11-07.md). Re-run this audit as branches, CI, code, and deployment state change; do not treat it as permanent truth.

## 1. Product north star

Developer OS is evolving into an **Autonomous Software Engineering Universe**: one connected environment in which a person or team can take software from an initial idea through planning, design, implementation, verification, deployment, operation, and continuous improvement.

The human remains the product owner and decision-maker. AI agents may perform delegated work within explicit permissions, budgets, and safety boundaries. Autonomy must be earned through reliable execution and evidence; it must never be simulated by UI claims.

**Product promise:** One workspace for humans and AI agents to plan, build, verify, ship, and operate software.

## 2. Experience principles

1. **One shared project reality:** Manual, AI-assisted, and hybrid work operate on the same files, repository state, tasks, decisions, and history.
2. **Human-directed autonomy:** Users can inspect plans, approve risky actions, pause agents, roll back changes, and see exactly what happened.
3. **Durable by design:** Saves, commits, agent actions, and deployment state must be persisted and recoverable; a browser cache is not the source of truth.
4. **Secure execution by default:** Untrusted code runs only in isolated, resource-limited environments. If isolation cannot be established, execution fails closed.
5. **Evidence over claims:** A capability is not complete until automated tests and deployment-level acceptance demonstrate it.
6. **Incremental evolution:** Preserve healthy modules and APIs. Prefer adapters, bounded services, and backward-compatible migrations over rewrites.
7. **Useful before autonomous:** Every layer must provide value in manual mode before relying on AI automation.

## 3. The three development modes

- **Manual Build:** The developer edits and controls the project directly.
- **AI Agent Build:** The user delegates a defined objective to an agent workflow.
- **Hybrid Build:** The developer and agents work concurrently against the same versioned workspace, with explicit ownership and conflict handling.

Switching modes must not fork project data or lose unsaved work. Agent changes must be reviewable diffs or commits, not invisible mutations.

## 4. Target product capabilities

### A. Unified Engineering Workspace
- Professional multi-file IDE, explorer, search, terminal, diagnostics, and debugger integration.
- Project planning, tasks, notes, documentation, decisions, and activity.
- Repository, branch, commit, diff, and rollback workflows.
- Preview, environment configuration, deployment controls, logs, and monitoring.
- Accessible responsive experience across supported devices.

### B. Durable Workspace and Project Memory
- Server-authoritative file persistence and versioned snapshots.
- Autosave with explicit save state, retry, and conflict resolution.
- Offline queue where feasible, with deterministic reconciliation.
- Crash recovery, backups, restore points, and audit history.
- Project knowledge index grounded in actual files, discussions, decisions, and execution history.
- Clear retention, export, and deletion controls.

### C. Secure Execution and Delivery Fabric
- Isolated runner/worker with per-job identity and authorization.
- CPU, memory, time, process, disk, and network limits.
- Workspace-scoped filesystem and secret injection that avoids exposing credentials to untrusted code.
- Job queue, cancellation, lifecycle events, logs, artifacts, and reproducible environments.
- Separate, explicitly secured network bridges for package installation and preview.
- Build/test artifacts and deployment adapters with rollback support.

### D. Agent Orchestration
- Central orchestrator converts a user objective into a plan, tasks, dependencies, and acceptance criteria.
- Specialized roles may include product planner, architect, frontend, backend, database, test, security, DevOps, and reviewer agents.
- Agents receive only the minimum project context and permissions required.
- Shared task state, bounded concurrency, budget/time limits, cancellation, retries, and idempotency.
- File ownership/locking or merge-based coordination to prevent agents overwriting one another.
- Human approval gates for destructive operations, external publication, production deployment, spending, and secret access.
- Every agent action is traceable to an actor, input, tool, result, and project revision.

### E. End-to-End Software Lifecycle
- Discover requirements and clarify unknowns.
- Produce architecture, data models, interfaces, and implementation plans.
- Generate and modify code in reviewable increments.
- Run tests, static analysis, security checks, and quality gates.
- Diagnose failures and propose or apply bounded fixes.
- Prepare deployment, verify health, observe production, and support rollback.
- Convert incidents and feedback into prioritized improvements.

### F. Project Digital Twin
A continuously updated, evidence-backed model of project state: architecture, dependencies, files, tests, deployments, risks, open work, and operational health. It must identify stale or uncertain data rather than inventing certainty.

### G. SaaS and Ecosystem
- Individual, team, business, and enterprise workspaces.
- Plans and metered entitlements tied to actual resource consumption.
- Organization roles, auditability, billing-provider webhook verification, and usage transparency.
- Extensible integrations through permission-scoped adapters and documented APIs.

## 5. Delivery roadmap and acceptance gates

Work proceeds in dependency order. Dates are intentionally not promised until capacity, infrastructure, and baseline quality are measured.

### Phase 0 — Baseline, safety, and release truth
**Goal:** Establish what works today and protect it.

- Inventory current routes, services, models, migrations, jobs, integrations, and deployment configuration.
- Map each roadmap capability to existing implementation, partial implementation, missing work, and evidence.
- Identify open PRs, branch drift, CI status, production incidents, and known infrastructure blockers.
- Establish critical user journeys and regression tests.
- Keep production readiness separate from feature completeness.

**Exit gate:** Reproducible baseline report; critical tests and deployment health are visible; no unresolved high-severity regression is hidden by a readiness flag.

### Phase 1 — Reliable unified workspace
**Goal:** Make the existing workspace and Web IDE dependable.

- Fix persistence, save-state visibility, stale-write protection, and conflict recovery.
- Validate multi-file editing, navigation, search, terminal lifecycle, and workspace permissions.
- Connect repository operations and IDE files to one authoritative project state.
- Improve accessibility, responsiveness, error recovery, and performance.
- Add end-to-end tests for create, edit, save, reload, branch, commit, and restore.

**Exit gate:** A user can edit multiple files, leave/reopen the workspace, and recover the exact persisted state; concurrent edits cannot silently overwrite data.

### Phase 2 — Secure execution fabric
**Goal:** Turn execution intent into safe, observable jobs.

- Complete isolated runner provisioning and strict capability checks.
- Implement authenticated job submission, queueing, cancellation, timeouts, resource quotas, and logs.
- Verify workspace mount isolation and safe artifact retrieval.
- Implement package/network and preview bridges only after threat modeling and isolation tests.
- Add abuse controls and operational dashboards.

**Exit gate:** Adversarial tests demonstrate tenant isolation, resource enforcement, cancellation, and fail-closed behavior. No arbitrary code executes in the web/API process.

### Phase 3 — Trusted AI engineering assistant
**Goal:** Make AI useful against real project context.

- Ground responses in authorized project files and durable project knowledge.
- Add structured plans, tool contracts, patch previews, and validation.
- Require diffs and test results for code changes.
- Track provider availability, model configuration, latency, token/resource cost, and failures.
- Provide explicit user approval and rollback.

**Exit gate:** AI can complete bounded engineering tasks on a test project with reproducible evidence, accurate status reporting, and no unreviewed destructive writes.

### Phase 4 — Multi-agent orchestration
**Goal:** Coordinate specialist agents safely.

- Introduce orchestrator state machine, task DAG, agent registry, and event log.
- Add bounded specialist roles and shared task contracts.
- Implement file/task ownership, conflict detection, retries, cancellation, and budget enforcement.
- Add review agent and human approval checkpoints.
- Compare multi-agent results against a single-agent baseline to justify complexity.

**Exit gate:** A multi-step task completes repeatably without lost work, duplicate side effects, uncontrolled cost, or hidden agent actions.

### Phase 5 — Full lifecycle automation and project digital twin
**Goal:** Connect planning through production operations.

- Integrate test/build/security/deploy pipelines.
- Add environment and deployment adapters with approval and rollback.
- Build evidence-backed project state and dependency/risk graph.
- Add operational telemetry, incident workflows, and safe remediation proposals.
- Support continuous improvement from verified project and production feedback.

**Exit gate:** A sample application can move from approved plan to verified deployment and monitored operation, with traceable artifacts and a tested rollback path.

### Phase 6 — Scale, ecosystem, and commercial maturity
**Goal:** Operate as a trustworthy global SaaS.

- Measure tenant isolation, reliability, latency, capacity, and unit economics.
- Implement transparent quotas, metering, billing reconciliation, and abuse prevention.
- Complete organization administration, audit exports, retention, backups, and disaster recovery.
- Add integration marketplace/API governance only where customer demand validates it.
- Run design-partner pilots and paid-customer validation before scaling acquisition.

**Exit gate:** Paid customers can use the service reliably; costs and limits are measurable; support, incident response, and recovery processes are exercised.

## 6. Non-negotiable architecture and security rules

- Do not execute arbitrary user code inside Django/API workers.
- Do not weaken readiness checks to make deployment appear healthy.
- Do not add an unsafe runner fallback when sandboxing is unavailable.
- Keep secrets out of source control, prompts, logs, and untrusted job environments.
- Enforce tenant and project authorization on every data and job boundary.
- Use idempotency for retried jobs and external side effects.
- Version APIs and database changes; provide safe migration and rollback plans.
- Use least-privilege service identities and auditable access.
- Treat AI output as untrusted until validated by tools, tests, and policy checks.
- Production deploy, destructive data actions, external publication, and paid resource use require explicit policy and user authorization.

## 7. How to prioritize work

Prioritize in this order:

1. Security, data integrity, and production incidents.
2. Reliability of existing critical workflows.
3. Durable workspace and secure execution dependencies.
4. AI capabilities that demonstrably reduce real engineering work.
5. Multi-agent features only after single-agent workflows are reliable.
6. Advanced autonomy and ecosystem expansion after customer validation.

Do not prioritize a flashy feature over a broken save path, insecure execution boundary, or unreliable deployment.

## 8. Definition of done

A task is done only when:
- Scope and affected components are identified.
- Existing behavior and compatibility are preserved or intentionally migrated.
- Tests cover success, failure, authorization, and relevant edge cases.
- Security and resource implications are reviewed.
- CI checks pass, or failures are explicitly documented.
- Deployment/configuration requirements are documented.
- User-visible status reflects actual capability and limitations.
- Evidence is attached to the PR or release report.

## 9. Current-state honesty

This document defines the **target direction**, not a claim that all target capabilities exist.

The current repository already documents a substantial foundation: React/Vite and Django/DRF application, project/workspace features, AI assistant integration boundary, GitHub integration, an independent repository layer, Web IDE surfaces, SaaS foundations, and production/security tooling.

The repository documentation also explicitly states that the Web IDE does not execute arbitrary user code in the Django process; execution requires an isolated runner/worker integration. AI, billing, GitHub, cache, email, and other integrations depend on real configuration and deployment validation.

Therefore, each phase must begin with a code-and-deployment audit. A feature must be labeled **implemented**, **partial**, **planned**, or **blocked**, with tests or live evidence. Never infer production readiness from a README, UI, branch name, or successful build alone.

## 10. Immediate execution priorities

1. Reconcile open PRs and branch changes without bypassing review or protected-branch rules.
2. Establish the current master CI and live deployment baseline.
3. Resolve critical persistence, readiness, Runner, and operational blockers.
4. Validate the Web IDE's end-to-end save and execution contracts.
5. Only then expand AI task execution and orchestration.

**North star:** Think. Plan. Build. Verify. Deploy. Operate. Evolve — in one universe, with humans in control.


## 11. Implementation progress — 2026-11-07

**Implemented on the isolated PR branch:** persistent `AgentRun`/`AgentTask` records, idempotent asynchronous plan submission through the existing durable `BackgroundJob` worker, owner-scoped status/cancel/approve endpoints, and validation of bounded task dependency graphs (unique IDs, known dependencies, topological order, no cycles). The planning worker rechecks workspace write access and revision before and after the provider call, excludes common secret-bearing files from provider context, and never executes user code or mutates workspace files.

**Still planned:** approved task execution, specialist agent/tool registry, per-tool capability grants, task-level approval, safe patch generation/application, isolated Runner execution, verification/retry loops, resumable multi-step runs, and production deployment/monitoring automation. Plan approval currently approves the plan only; the API explicitly reports `plan_only_no_workspace_mutations`.

**Deployment prerequisite:** apply migration `0035_agent_run_task_graph` before enabling the new endpoints. Confirm the backend worker is deployed and processing `ai_agent_plan` jobs. No production migration or live provider execution is claimed by this branch update.
