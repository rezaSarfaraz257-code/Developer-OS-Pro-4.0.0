import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, formatApiError } from "./services/api";
import {
  executionStorageKey,
  getAcceptedCriteria,
  getExecutionPollDelay,
  isSyncTerminal,
  normalizeExecutionId,
  normalizeSavedSyncState,
  normalizeSyncSnapshot,
  normalizeTaskExecution,
} from "./agentExecutionWorkflow";

const VERIFICATION_PROFILES = [
  ["python_pytest", "Python · Pytest"],
  ["python_unittest", "Python · unittest"],
  ["node_test", "Node.js · npm test"],
  ["node_build", "Node.js · production build"],
  ["node_lint", "Node.js · lint"],
];

function errorMessage(payload, fallback) {
  return formatApiError(payload, fallback);
}

function readSavedSync(runId, taskId, workspaceId) {
  const key = executionStorageKey(runId, taskId);
  if (!key) return null;
  try {
    const saved = JSON.parse(window.sessionStorage.getItem(key) || "null");
    const normalized = normalizeSavedSyncState(saved, { runId, taskId, workspaceId });
    return normalized ? { ...normalized, runId, taskId } : null;
  } catch {
    return null;
  }
}

function persistSync(runId, taskId, sync) {
  const key = executionStorageKey(runId, taskId);
  if (!key || !sync) return;
  try {
    window.sessionStorage.setItem(key, JSON.stringify({ runId, taskId, sync }));
  } catch {
    // Session storage is optional. The active execution remains usable in memory.
  }
}

export default function AgentTaskExecutionWorkflow({ run, onRefreshRun }) {
  const tasks = Array.isArray(run?.tasks) ? run.tasks : [];
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [execution, setExecution] = useState(null);
  const [executionLoaded, setExecutionLoaded] = useState(false);
  const [proposal, setProposal] = useState(null);
  const [syncJob, setSyncJob] = useState(null);
  const [profile, setProfile] = useState("");
  const [reviewConfirmed, setReviewConfirmed] = useState(false);
  const [acceptedSelection, setAcceptedSelection] = useState([]);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const statusSequenceRef = useRef(0);
  const actionLockRef = useRef(false);

  useEffect(() => {
    if (!tasks.length) {
      setSelectedTaskId("");
      return;
    }
    setSelectedTaskId((current) => (
      tasks.some((task) => String(task?.id) === current)
        ? current
        : String((tasks.find((task) => ["ready", "failed", "running", "awaiting_approval"].includes(task?.status)) || tasks[0]).id)
    ));
  }, [run?.id, tasks]);

  const selectedTask = useMemo(
    () => tasks.find((task) => String(task?.id) === selectedTaskId) || null,
    [tasks, selectedTaskId],
  );
  const taskId = normalizeExecutionId(selectedTask?.id);
  const runId = normalizeExecutionId(run?.id);
  const workspaceId = normalizeExecutionId(run?.workspace);

  useEffect(() => {
    const sequence = ++statusSequenceRef.current;
    let active = true;
    setExecution(null);
    setExecutionLoaded(false);
    setProposal(null);
    setReviewConfirmed(false);
    setAcceptedSelection([]);
    setError("");
    setNotice("");
    const restoredSync = readSavedSync(runId, taskId, workspaceId);
    setSyncJob(restoredSync);
    if (runId === null || taskId === null) return () => { active = false; };
    apiFetch(`/ai/agent/runs/${runId}/tasks/${taskId}/verification/`)
      .then(async (response) => {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorMessage(payload, "Task execution status could not be loaded."));
        const normalized = normalizeTaskExecution(payload, { runId, taskId });
        if (!normalized) throw new Error("The server returned an invalid task execution identity.");
        if (active && sequence === statusSequenceRef.current) {
          setExecution(normalized);
          setExecutionLoaded(true);
          if (normalized.evidence.sync_job_id && normalized.revision) {
            try {
              const syncResponse = await apiFetch(`/ai/agent/sync-jobs/${normalized.evidence.sync_job_id}/`);
              const syncPayload = await syncResponse.json().catch(() => ({}));
              if (!syncResponse.ok) throw new Error(errorMessage(syncPayload, "Saved Runner synchronization evidence could not be restored."));
              const restored = normalizeSyncSnapshot(syncPayload, { workspaceId });
              if (!restored || restored.id !== normalized.evidence.sync_job_id) {
                throw new Error("Saved Runner synchronization identity or workspace could not be verified.");
              }
              const revisionMismatch = syncPayload.revision == null
                || normalizeExecutionId(syncPayload.revision) !== normalized.revision
                || syncPayload.workspace_id == null
                || normalizeExecutionId(syncPayload.workspace_id) !== workspaceId;
              const nextSync = {
                ...restored,
                revision: restored.revision ?? normalized.revision,
                status: revisionMismatch && restored.status === "succeeded" ? "stale" : restored.status,
              };
              if (active && sequence === statusSequenceRef.current) {
                setSyncJob(nextSync);
                persistSync(runId, taskId, nextSync);
              }
            } catch (syncError) {
              if (active && sequence === statusSequenceRef.current) {
                setError(syncError?.message || "Saved Runner synchronization evidence could not be restored.");
              }
            }
          }
        }
      })
      .catch((requestError) => {
        if (active && sequence === statusSequenceRef.current) {
          setExecutionLoaded(false);
          setError(requestError?.message || "Task execution status could not be loaded.");
        }
      });
    return () => {
      active = false;
      statusSequenceRef.current += 1;
    };
  }, [runId, taskId, workspaceId]);

  const syncJobId = syncJob?.id;
  const syncJobStatus = syncJob?.status;
  const syncJobWorkspaceId = syncJob?.workspaceId;
  const syncJobRevision = syncJob?.revision;
  const syncJobCoalesced = syncJob?.coalesced;
  const syncJobError = syncJob?.error;
  const syncJobSnapshot = useMemo(() => (
    syncJobId == null ? null : {
      id: syncJobId,
      status: syncJobStatus,
      workspaceId: syncJobWorkspaceId,
      revision: syncJobRevision,
      coalesced: syncJobCoalesced,
      error: syncJobError,
    }
  ), [syncJobId, syncJobStatus, syncJobWorkspaceId, syncJobRevision, syncJobCoalesced, syncJobError]);

  useEffect(() => {
    const currentSync = syncJobSnapshot;
    if (!currentSync || isSyncTerminal(currentSync.status) || !currentSync.id || runId === null) return undefined;
    let active = true;
    let timer;
    let failures = 0;
    const poll = async () => {
      try {
        const response = await apiFetch(`/ai/agent/sync-jobs/${currentSync.id}/`);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorMessage(payload, "Runner synchronization status is unavailable."));
        const normalized = normalizeSyncSnapshot(payload, { workspaceId });
        if (!normalized || normalized.id !== currentSync.id) {
          const invalidReceipt = {
            ...currentSync,
            status: "stale",
            error: "Runner synchronization identity or workspace could not be verified.",
          };
          if (active) {
            setSyncJob(invalidReceipt);
            persistSync(runId, taskId, invalidReceipt);
            setError(invalidReceipt.error);
          }
          return;
        }
        const revisionMismatch = payload.revision == null
          || normalizeExecutionId(payload.revision) !== normalizeExecutionId(currentSync.revision)
          || payload.workspace_id == null
          || normalizeExecutionId(payload.workspace_id) !== workspaceId;
        const next = {
          ...normalized,
          revision: normalized.revision ?? currentSync.revision,
          status: revisionMismatch && normalized.status === "succeeded" ? "stale" : normalized.status,
        };
        if (!active) return;
        failures = 0;
        setSyncJob(next);
        persistSync(runId, taskId, next);
        if (!isSyncTerminal(next.status)) {
          timer = window.setTimeout(poll, getExecutionPollDelay(0, document.visibilityState === "hidden"));
        } else if (next.status === "succeeded") {
          setNotice("The exact approved workspace revision is synchronized with Runner.");
          onRefreshRun?.(runId, { quiet: true });
        } else if (next.status === "stale") {
          setError("Runner synchronized a different workspace revision. Do not execute this task; generate a fresh proposal for the current revision.");
        } else {
          setError(next.error || "Runner synchronization failed. Verification is blocked until the workspace is safely synchronized.");
        }
      } catch (pollError) {
        if (!active) return;
        failures += 1;
        setError(pollError?.message || "Runner synchronization status could not be refreshed.");
        timer = window.setTimeout(poll, getExecutionPollDelay(failures, document.visibilityState === "hidden"));
      }
    };
    timer = window.setTimeout(poll, 900);
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [syncJobSnapshot, runId, taskId, workspaceId, onRefreshRun]);

  const executionStatus = execution?.status;

  useEffect(() => {
    if (executionStatus !== "running" || runId === null || taskId === null) return undefined;
    let active = true;
    let timer;
    let failures = 0;
    const poll = async () => {
      try {
        const response = await apiFetch(`/ai/agent/runs/${runId}/tasks/${taskId}/verification/`);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorMessage(payload, "Runner verification status is unavailable."));
        const normalized = normalizeTaskExecution(payload, { runId, taskId });
        if (!normalized) throw new Error("The verification response does not match the selected Agent task.");
        if (!active) return;
        failures = 0;
        setExecution(normalized);
        if (normalized.status === "running") {
          timer = window.setTimeout(poll, getExecutionPollDelay(0, document.visibilityState === "hidden"));
        } else {
          setNotice(normalized.status === "awaiting_approval"
            ? "Runner verification passed. Review the evidence and explicitly accept every task criterion."
            : normalized.status === "failed"
              ? "Runner verification failed. Review the diagnostic evidence before requesting a repair proposal."
              : "Task execution state updated.");
          onRefreshRun?.(runId, { quiet: true });
        }
      } catch (pollError) {
        if (!active) return;
        failures += 1;
        setError(pollError?.message || "Runner verification status could not be refreshed.");
        timer = window.setTimeout(poll, getExecutionPollDelay(failures, document.visibilityState === "hidden"));
      }
    };
    timer = window.setTimeout(poll, 1100);
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [executionStatus, runId, taskId, onRefreshRun]);

  const effectiveStatus = execution?.status || selectedTask?.status || "unknown";
  const criteria = execution?.acceptanceCriteria?.length
    ? execution.acceptanceCriteria
    : Array.isArray(selectedTask?.acceptance_criteria) ? selectedTask.acceptance_criteria : [];
  const canPropose = Boolean(
    selectedTask && runId && taskId && executionLoaded
    && (!proposal || proposal.applied)
    && (!syncJob || syncJob.status === "stale")
    && ["approved", "running"].includes(run?.status)
    && effectiveStatus === "ready",
  );
  const canRepair = Boolean(
    selectedTask && runId && taskId && executionLoaded
    && (!proposal || proposal.applied)
    && run?.status === "failed"
    && effectiveStatus === "failed"
    && syncJob?.status === "succeeded"
    && syncJob.revision === execution?.revision
    && execution?.evidence?.repair_analysis?.status === "review_required",
  );
  const canVerify = Boolean(
    syncJob?.status === "succeeded"
    && syncJob.revision
    && workspaceId
    && ["approved", "running", "failed"].includes(run?.status)
    && (run?.status !== "failed" || effectiveStatus === "failed")
    && executionLoaded
    && ["ready", "failed"].includes(effectiveStatus)
    && (!execution?.revision || syncJob.revision > execution.revision)
    && profile,
  );
  const allCriteriaAccepted = criteria.length > 0
    && getAcceptedCriteria(criteria, acceptedSelection).length === criteria.length;

  async function requestProposal(kind) {
    if (actionLockRef.current || (!canPropose && !(kind === "repair" && canRepair))) return;
    actionLockRef.current = true;
    setBusyAction(kind === "repair" ? "repair" : "proposal");
    setError("");
    setNotice("");
    setProposal(null);
    setReviewConfirmed(false);
    try {
      const suffix = kind === "repair" ? "repair-proposal" : "proposal";
      const response = await apiFetch(`/ai/agent/runs/${runId}/tasks/${taskId}/${suffix}/`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorMessage(payload, "The implementation proposal could not be generated."));
      const revision = normalizeExecutionId(payload.workspace_revision);
      const approvalToken = typeof payload.approval_token === "string" && payload.approval_token.length <= 50000
        ? payload.approval_token
        : "";
      if (
        payload.status !== "proposal_ready"
        || payload.run_id !== runId
        || payload.task_id !== taskId
        || revision === null
        || !approvalToken
        || !payload.proposal
        || !Array.isArray(payload.proposal.changes)
        || !payload.proposal.changes.length
        || payload.proposal.requires_approval !== true
        || payload.workspace_mutated !== false
      ) {
        throw new Error("The server returned an incomplete or unbound patch proposal. No changes were applied.");
      }
      setProposal({
        kind,
        revision,
        approvalToken,
        value: payload.proposal,
        applied: false,
      });
      setNotice("Proposal generated without changing the workspace. Review every affected file before approving it.");
    } catch (requestError) {
      setError(requestError?.message || "The implementation proposal could not be generated.");
    } finally {
      actionLockRef.current = false;
      setBusyAction("");
    }
  }

  async function applyApprovedPatch() {
    if (actionLockRef.current || !proposal?.approvalToken || !reviewConfirmed || proposal.applied) return;
    actionLockRef.current = true;
    setBusyAction("apply");
    setError("");
    setNotice("");
    try {
      const response = await apiFetch("/ai/agent/apply/", {
        method: "POST",
        body: JSON.stringify({ approval_token: proposal.approvalToken }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorMessage(payload, "The approved patch could not be applied."));
      const syncId = normalizeExecutionId(payload.sync?.job_id);
      const appliedWorkspaceId = normalizeExecutionId(payload.workspace?.id);
      const revision = normalizeExecutionId(payload.workspace?.revision);
      if (
        payload.status !== "applied_sync_pending"
        || syncId === null
        || appliedWorkspaceId !== workspaceId
        || revision === null
        || revision <= proposal.revision
      ) {
        throw new Error("Patch application returned no verifiable workspace revision or synchronization job. Reload the run before continuing.");
      }
      const nextSync = {
        id: syncId,
        status: typeof payload.sync.status === "string" ? payload.sync.status : "queued",
        workspaceId: appliedWorkspaceId,
        revision,
        coalesced: false,
        error: "",
      };
      setProposal((current) => current ? { ...current, approvalToken: "", applied: true } : current);
      setSyncJob(nextSync);
      persistSync(runId, taskId, nextSync);
      setNotice("Human-approved patch applied. Waiting for Runner to synchronize the exact new revision.");
      await onRefreshRun?.(runId, { quiet: true });
    } catch (requestError) {
      setError(requestError?.message || "The approved patch could not be applied.");
    } finally {
      actionLockRef.current = false;
      setBusyAction("");
    }
  }

  async function startVerification() {
    if (actionLockRef.current || !canVerify) return;
    actionLockRef.current = true;
    setBusyAction("verify");
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(`/ai/agent/runs/${runId}/tasks/${taskId}/verify/`, {
        method: "POST",
        body: JSON.stringify({
          profile,
          sync_job_id: syncJob.id,
          revision: syncJob.revision,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorMessage(payload, "Runner verification could not be queued."));
      const normalized = normalizeTaskExecution(payload, { runId, taskId });
      if (!normalized || !normalized.job?.id || !["running", "awaiting_approval"].includes(normalized.status)) {
        throw new Error("The server did not confirm a valid verification job for this Agent task.");
      }
      setExecution(normalized);
      setNotice("Approved verification queued in the isolated Runner. The result will be polled from the durable task record.");
      await onRefreshRun?.(runId, { quiet: true });
    } catch (requestError) {
      setError(requestError?.message || "Runner verification could not be queued.");
    } finally {
      actionLockRef.current = false;
      setBusyAction("");
    }
  }

  async function acceptTask() {
    if (actionLockRef.current || effectiveStatus !== "awaiting_approval" || !allCriteriaAccepted) return;
    actionLockRef.current = true;
    setBusyAction("accept");
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(`/ai/agent/runs/${runId}/tasks/${taskId}/accept/`, {
        method: "POST",
        body: JSON.stringify({ accepted_criteria: getAcceptedCriteria(criteria, acceptedSelection) }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorMessage(payload, "Task acceptance could not be recorded."));
      const normalized = normalizeTaskExecution(payload, { runId, taskId });
      if (!normalized || normalized.status !== "succeeded" || !normalized.evidence?.human_acceptance) {
        throw new Error("The server did not return durable human-acceptance evidence. The task is not shown as complete.");
      }
      setExecution(normalized);
      setAcceptedSelection([]);
      setNotice("Task accepted. Its dependent tasks can now become ready.");
      await onRefreshRun?.(runId, { quiet: true });
    } catch (requestError) {
      setError(requestError?.message || "Task acceptance could not be recorded.");
    } finally {
      actionLockRef.current = false;
      setBusyAction("");
    }
  }

  if (!tasks.length) return null;

  return (
    <section className="cc-agent-execution" aria-labelledby="cc-agent-execution-title">
      <div className="cc-agent-execution-head">
        <div>
          <span className="cc-mode-detail-label">APPROVAL-GATED EXECUTION</span>
          <h3 id="cc-agent-execution-title">Task engineering loop</h3>
          <p>Proposal → human approval → workspace sync → isolated verification → human acceptance.</p>
        </div>
        <span className="cc-agent-execution-count">{tasks.length} task{tasks.length === 1 ? "" : "s"}</span>
      </div>

      <label className="cc-agent-execution-select">
        <span>SELECT TASK</span>
        <select value={selectedTaskId} onChange={(event) => setSelectedTaskId(event.target.value)} disabled={Boolean(busyAction)}>
          {tasks.map((task) => (
            <option key={String(task.id)} value={String(task.id)}>
              {task.title || "Untitled task"} · {task.status || "unknown"}
            </option>
          ))}
        </select>
      </label>

      {selectedTask && (
        <div className="cc-agent-execution-task">
          <div className="cc-agent-execution-task-head">
            <div>
              <strong>{selectedTask.title}</strong>
              <small>{String(selectedTask.agent_role || "engineering").replaceAll("_", " ")} · {effectiveStatus}</small>
            </div>
            <span className={`cc-agent-execution-state is-${effectiveStatus}`}>{effectiveStatus.replaceAll("_", " ")}</span>
          </div>
          {selectedTask.description && <p>{selectedTask.description}</p>}
          {Array.isArray(selectedTask.depends_on) && selectedTask.depends_on.length > 0 && (
            <small className="cc-agent-execution-dependencies">Prerequisites: {selectedTask.depends_on.join(", ")}</small>
          )}

          <div className="cc-agent-execution-actions">
            {canPropose && <button type="button" onClick={() => requestProposal("implementation")} disabled={Boolean(busyAction)}> {busyAction === "proposal" ? "Generating…" : "Generate implementation proposal"} </button>}
            {canRepair && <button type="button" onClick={() => requestProposal("repair")} disabled={Boolean(busyAction)}> {busyAction === "repair" ? "Analyzing failure…" : "Generate bounded repair proposal"} </button>}
            {executionLoaded && effectiveStatus === "failed" && !canRepair && <p>Failure evidence is not eligible for automated repair. Manual review is required; no speculative patch will be generated.</p>}
            {!executionLoaded && <p>Loading authoritative task execution state…</p>}
            {effectiveStatus === "blocked" && <p>Prerequisite tasks must be accepted before this task can be proposed or verified.</p>}
            {effectiveStatus === "running" && <p>Runner verification is active. Status is refreshed from the durable task record.</p>}
            {effectiveStatus === "succeeded" && <p className="is-success">Task completion is backed by recorded Runner evidence and explicit human acceptance.</p>}
          </div>

          {proposal && (
            <div className="cc-agent-proposal">
              <div className="cc-agent-proposal-head">
                <div><span className="cc-mode-detail-label">{proposal.kind === "repair" ? "REPAIR PROPOSAL" : "IMPLEMENTATION PROPOSAL"}</span><strong>{proposal.value.summary}</strong></div>
                <span>Revision {proposal.revision}</span>
              </div>
              {proposal.value.root_cause && <p><b>Root cause:</b> {proposal.value.root_cause}</p>}
              {Array.isArray(proposal.value.affected_files) && <p><b>Affected files:</b> {proposal.value.affected_files.join(", ")}</p>}
              {Array.isArray(proposal.value.risks) && proposal.value.risks.length > 0 && <div><b>Risks</b><ul>{proposal.value.risks.map((item, index) => <li key={index}>{item}</li>)}</ul></div>}
              {Array.isArray(proposal.value.test_plan) && proposal.value.test_plan.length > 0 && <div><b>Advisory test plan</b><ul>{proposal.value.test_plan.map((item, index) => <li key={index}>{item}</li>)}</ul></div>}
              <div className="cc-agent-proposal-files">
                {proposal.value.changes.map((change, index) => (
                  <details key={change.path || index}>
                    <summary><span>{change.operation}</span> {change.path}</summary>
                    {change.reason && <p>{change.reason}</p>}
                    {typeof change.content === "string" && <pre>{change.content}</pre>}
                  </details>
                ))}
              </div>
              {!proposal.applied ? (
                <div className="cc-agent-approval">
                  <label><input type="checkbox" checked={reviewConfirmed} onChange={(event) => setReviewConfirmed(event.target.checked)} disabled={Boolean(busyAction)} /> I reviewed the complete patch, affected paths, and risks. I explicitly approve applying this revision.</label>
                  <button type="button" onClick={applyApprovedPatch} disabled={!reviewConfirmed || !proposal.approvalToken || Boolean(busyAction)}>
                    {busyAction === "apply" ? "Applying approved patch…" : "Approve & apply patch"}
                  </button>
                  <small>The approval token stays in memory only and is discarded after application. No unattended workspace mutation is allowed.</small>
                </div>
              ) : <p className="is-success">Patch approval consumed. The workspace was updated; Runner synchronization is tracked separately below.</p>}
            </div>
          )}

          {syncJob && (
            <div className="cc-agent-sync">
              <div><b>RUNNER SYNCHRONIZATION</b><span>Job #{syncJob.id} · Revision {syncJob.revision}</span></div>
              <strong className={`is-${syncJob.status}`}>{syncJob.status.replaceAll("_", " ")}</strong>
              {syncJob.coalesced && <p>Runner synchronized a newer workspace snapshot. This task cannot verify the older patch revision.</p>}
              {syncJob.status === "stale" && <p>Revision mismatch: create a new proposal from the current workspace before running tests.</p>}
              {syncJob.error && <p>{syncJob.error}</p>}
            </div>
          )}

          {syncJob?.status === "succeeded" && (
            <div className="cc-agent-verification">
              <label><span>FIXED VERIFICATION PROFILE</span>
                <select value={profile} onChange={(event) => setProfile(event.target.value)} disabled={Boolean(busyAction)}>
                  <option value="">Choose a server-approved profile</option>
                  {VERIFICATION_PROFILES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <p>Commands are selected from a server-side allowlist. Arbitrary shell text is never sent by this workflow.</p>
              <button type="button" onClick={startVerification} disabled={!canVerify || Boolean(busyAction)}>
                {busyAction === "verify" ? "Queueing verification…" : "Run approved build / test"}
              </button>
            </div>
          )}

          {execution?.evidence && (execution.evidence.state || execution.evidence.revision || execution.evidence.verification_job_id) && (
            <div className="cc-agent-evidence">
              <b>VERIFICATION EVIDENCE</b>
              <div className="cc-agent-evidence-grid">
                <span>State <strong>{execution.evidence.state || "unknown"}</strong></span>
                <span>Runner <strong>{execution.evidence.runner_status || "—"}</strong></span>
                <span>Exit code <strong>{execution.evidence.exit_code ?? "—"}</strong></span>
                <span>Duration <strong>{Number.isFinite(execution.evidence.duration_ms) ? `${execution.evidence.duration_ms} ms` : "—"}</strong></span>
                <span>Revision <strong>{execution.evidence.revision ?? "—"}</strong></span>
                <span>Iteration <strong>{execution.evidence.iteration ?? "—"}</strong></span>
              </div>
              {Array.isArray(execution.evidence.diagnostic_signals) && execution.evidence.diagnostic_signals.length > 0 && (
                <div className="cc-agent-diagnostics"><b>Allowlisted diagnostics</b>{execution.evidence.diagnostic_signals.map((signal, index) => <span key={index}>{signal.code} · {signal.source}</span>)}</div>
              )}
              {execution.evidence.history.length > 0 && (
                <div className="cc-agent-attempt-history">
                  <b>PREVIOUS VERIFICATION ATTEMPTS</b>
                  {execution.evidence.history.map((attempt, index) => (
                    <span key={index}>Attempt {attempt.iteration ?? index + 1} · revision {attempt.revision ?? "—"} · {attempt.runner_status || attempt.state || "unknown"} · exit {attempt.exit_code ?? "—"}</span>
                  ))}
                </div>
              )}
              <div className="cc-agent-audit-refs">
                <span>Verification job #{execution.evidence.verification_job_id ?? "—"}</span>
                <span>Sync job #{execution.evidence.sync_job_id ?? "—"}</span>
                <span>Patch audit #{execution.evidence.patch_audit_id ?? "—"}</span>
              </div>
              {execution.evidence.repair_analysis?.status && <p>Repair analysis: {execution.evidence.repair_analysis.status} · {execution.evidence.repair_analysis.diagnostic_code || "manual review"}</p>}
              <small>Raw process output and environment data are not stored in this evidence view.</small>
            </div>
          )}

          {effectiveStatus === "awaiting_approval" && (
            <div className="cc-agent-acceptance">
              <b>HUMAN ACCEPTANCE REQUIRED</b>
              <p>Runner passed. Confirm each acceptance criterion only after reviewing the result.</p>
              {criteria.map((criterion) => (
                <label key={criterion}><input
                  type="checkbox"
                  checked={acceptedSelection.includes(criterion)}
                  onChange={(event) => setAcceptedSelection((current) => event.target.checked
                    ? [...current, criterion]
                    : current.filter((item) => item !== criterion))}
                  disabled={Boolean(busyAction)}
                /> {criterion}</label>
              ))}
              <button type="button" onClick={acceptTask} disabled={!allCriteriaAccepted || Boolean(busyAction)}>
                {busyAction === "accept" ? "Recording acceptance…" : "Accept verified task"}
              </button>
            </div>
          )}
        </div>
      )}

      {error && <p className="cc-agent-message is-error" role="alert">{error}</p>}
      {notice && <p className="cc-agent-message" role="status" aria-live="polite">{notice}</p>}
    </section>
  );
}
