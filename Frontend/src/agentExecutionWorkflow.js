export function normalizeExecutionId(value) {
  const parsed = typeof value === "number"
    ? value
    : typeof value === "string" && /^[0-9]{1,19}$/.test(value.trim())
      ? Number(value.trim())
      : NaN;
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

export function normalizeSyncSnapshot(payload, expected = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const id = normalizeExecutionId(payload.job_id ?? payload.id);
  if (id === null) return null;
  const workspaceId = normalizeExecutionId(payload.workspace_id ?? expected.workspaceId);
  const revision = normalizeExecutionId(payload.revision);
  if (expected.workspaceId != null && workspaceId !== normalizeExecutionId(expected.workspaceId)) return null;
  const statuses = new Set(["queued", "running", "succeeded", "failed", "canceled", "stale"]);
  return {
    id,
    status: statuses.has(payload.status) ? payload.status : "unknown",
    workspaceId,
    revision,
    coalesced: payload.coalesced === true,
    error: typeof payload.error === "string" ? payload.error.slice(0, 500) : "",
  };
}

function normalizeEvidenceEntry(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const signals = Array.isArray(value.diagnostic_signals)
    ? value.diagnostic_signals.filter((signal) => signal && typeof signal === "object" && !Array.isArray(signal))
      .slice(0, 4)
      .map((signal) => ({
        code: typeof signal.code === "string" ? signal.code.slice(0, 64) : "unknown",
        source: signal.source === "stdout" || signal.source === "stderr" ? signal.source : "unknown",
      }))
    : [];
  return {
    state: typeof value.state === "string" ? value.state.slice(0, 40) : "",
    profile: typeof value.profile === "string" ? value.profile.slice(0, 32) : "",
    revision: normalizeExecutionId(value.revision),
    runner_status: typeof value.runner_status === "string" ? value.runner_status.slice(0, 32) : "",
    exit_code: Number.isSafeInteger(value.exit_code) ? value.exit_code : null,
    duration_ms: Number.isSafeInteger(value.duration_ms) && value.duration_ms >= 0 ? value.duration_ms : null,
    iteration: Number.isSafeInteger(value.iteration) && value.iteration > 0 ? value.iteration : null,
    verified_at: typeof value.verified_at === "string" ? value.verified_at.slice(0, 64) : "",
    diagnostic_signals: signals,
  };
}

export function normalizeTaskExecution(payload, expected = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const runId = normalizeExecutionId(payload.run_id);
  const taskId = normalizeExecutionId(payload.task_id);
  if (runId === null || taskId === null) return null;
  if (expected.runId != null && runId !== normalizeExecutionId(expected.runId)) return null;
  if (expected.taskId != null && taskId !== normalizeExecutionId(expected.taskId)) return null;
  const statuses = new Set(["blocked", "ready", "running", "awaiting_approval", "succeeded", "failed", "canceled"]);
  const job = payload.job && typeof payload.job === "object" && !Array.isArray(payload.job)
    ? {
        id: normalizeExecutionId(payload.job.id),
        status: typeof payload.job.status === "string" ? payload.job.status.slice(0, 32) : "unknown",
        attempts: Number.isSafeInteger(payload.job.attempts) && payload.job.attempts >= 0 ? payload.job.attempts : 0,
        maxAttempts: Number.isSafeInteger(payload.job.max_attempts) && payload.job.max_attempts >= 0 ? payload.job.max_attempts : 0,
      }
    : null;
  const rawEvidence = payload.evidence && typeof payload.evidence === "object" && !Array.isArray(payload.evidence)
    ? payload.evidence
    : {};
  const rawRepair = rawEvidence.repair_analysis && typeof rawEvidence.repair_analysis === "object"
    && !Array.isArray(rawEvidence.repair_analysis) ? rawEvidence.repair_analysis : {};
  const rawAcceptance = rawEvidence.human_acceptance && typeof rawEvidence.human_acceptance === "object"
    && !Array.isArray(rawEvidence.human_acceptance) ? rawEvidence.human_acceptance : null;
  const evidence = {
    state: typeof rawEvidence.state === "string" ? rawEvidence.state.slice(0, 40) : "",
    profile: typeof rawEvidence.profile === "string" ? rawEvidence.profile.slice(0, 32) : "",
    runner_status: typeof rawEvidence.runner_status === "string" ? rawEvidence.runner_status.slice(0, 32) : "",
    exit_code: Number.isSafeInteger(rawEvidence.exit_code) ? rawEvidence.exit_code : null,
    duration_ms: Number.isSafeInteger(rawEvidence.duration_ms) && rawEvidence.duration_ms >= 0 ? rawEvidence.duration_ms : null,
    revision: normalizeExecutionId(rawEvidence.revision),
    iteration: Number.isSafeInteger(rawEvidence.iteration) && rawEvidence.iteration > 0 ? rawEvidence.iteration : null,
    verification_job_id: normalizeExecutionId(rawEvidence.verification_job_id),
    sync_job_id: normalizeExecutionId(rawEvidence.sync_job_id),
    patch_audit_id: normalizeExecutionId(rawEvidence.patch_audit_id),
    runner_job_id: typeof rawEvidence.runner_job_id === "string" ? rawEvidence.runner_job_id.slice(0, 80) : "",
    verified_at: typeof rawEvidence.verified_at === "string" ? rawEvidence.verified_at.slice(0, 64) : "",
    output_persisted: rawEvidence.output_persisted === false ? false : null,
    history: Array.isArray(rawEvidence.history)
      ? rawEvidence.history.slice(0, 3).map(normalizeEvidenceEntry).filter(Boolean)
      : [],
    diagnostic_signals: Array.isArray(rawEvidence.diagnostic_signals)
      ? rawEvidence.diagnostic_signals.filter((signal) => signal && typeof signal === "object" && !Array.isArray(signal))
        .slice(0, 4)
        .map((signal) => ({
          code: typeof signal.code === "string" ? signal.code.slice(0, 64) : "unknown",
          source: signal.source === "stdout" || signal.source === "stderr" ? signal.source : "unknown",
        }))
      : [],
    repair_analysis: {
      status: typeof rawRepair.status === "string" ? rawRepair.status.slice(0, 40) : "",
      diagnostic_code: typeof rawRepair.diagnostic_code === "string" ? rawRepair.diagnostic_code.slice(0, 64) : "",
    },
    human_acceptance: rawAcceptance ? {
      user_id: normalizeExecutionId(rawAcceptance.user_id),
      accepted_at: typeof rawAcceptance.accepted_at === "string" ? rawAcceptance.accepted_at.slice(0, 64) : "",
      accepted_criteria: Array.isArray(rawAcceptance.accepted_criteria)
        ? rawAcceptance.accepted_criteria.filter((item) => typeof item === "string" && item.length <= 500).slice(0, 6)
        : [],
    } : null,
  };
  const criteria = Array.isArray(payload.acceptance_criteria)
    ? payload.acceptance_criteria.filter((item) => typeof item === "string" && item.trim() && item.length <= 500).slice(0, 6)
    : [];
  return {
    runId,
    taskId,
    status: statuses.has(payload.status) ? payload.status : "unknown",
    acceptanceCriteria: criteria,
    verificationProfile: typeof payload.verification_profile === "string" ? payload.verification_profile : "",
    revision: normalizeExecutionId(payload.revision),
    attempts: Number.isSafeInteger(payload.attempts) && payload.attempts >= 0 ? payload.attempts : 0,
    evidence,
    error: typeof payload.error === "string" ? payload.error.slice(0, 500) : "",
    job,
  };
}

export function isSyncTerminal(status) {
  return status === "succeeded" || status === "failed" || status === "canceled" || status === "stale" || status === "unknown";
}

export function isVerificationTerminal(status, jobStatus) {
  return ["awaiting_approval", "succeeded", "failed", "canceled", "blocked", "ready"].includes(status)
    || ["failed", "canceled"].includes(jobStatus);
}

export function getExecutionPollDelay(failures = 0, hidden = false) {
  if (hidden) return 15000;
  const count = Number.isFinite(failures) ? Math.max(0, Math.floor(failures)) : 0;
  return Math.min(12000, 2200 * (2 ** Math.min(count, 3)));
}

export function getAcceptedCriteria(criteria, selected) {
  if (!Array.isArray(criteria) || !Array.isArray(selected)) return [];
  const checked = new Set(selected);
  return criteria.filter((item) => typeof item === "string" && checked.has(item));
}

export function executionStorageKey(runId, taskId) {
  const run = normalizeExecutionId(runId);
  const task = normalizeExecutionId(taskId);
  return run && task ? `dos:agent-sync:${run}:${task}` : "";
}

export function normalizeSavedSyncState(value, expected = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const runId = normalizeExecutionId(value.runId);
  const taskId = normalizeExecutionId(value.taskId);
  if (runId !== normalizeExecutionId(expected.runId) || taskId !== normalizeExecutionId(expected.taskId)) return null;
  return normalizeSyncSnapshot(value.sync, expected);
}
