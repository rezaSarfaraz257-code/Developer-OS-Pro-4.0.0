const RUN_STATUSES = new Set(["queued", "planning", "awaiting_approval", "approved", "running", "succeeded", "failed", "canceled"]);

export function normalizeRunId(value) {
  const id = typeof value === "number" ? value
    : typeof value === "string" && /^\\d+$/.test(value.trim()) ? Number(value.trim()) : NaN;
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

export function normalizeAgentRun(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const runId = normalizeRunId(payload.id);
  if (runId === null) return null;
  const safeText = (value, fallback = "") => typeof value === "string" ? value : fallback;
  const safeScalar = (value) => typeof value === "string" || (typeof value === "number" && Number.isFinite(value)) ? value : null;
  const tasks = Array.isArray(payload.tasks) ? payload.tasks.filter((task) => task && typeof task === "object" && !Array.isArray(task)).map((task, index) => ({
    id: safeScalar(task.id) ?? `task-${index}`,
    title: safeText(task.title, `Task ${index + 1}`),
    agent_role: safeText(task.agent_role, "engineering"),
    status: safeText(task.status, "pending").replace(/[^a-z0-9_-]/gi, "").slice(0, 40) || "pending",
    depends_on: Array.isArray(task.depends_on) ? task.depends_on.filter((dependency) => typeof dependency === "string" || (typeof dependency === "number" && Number.isFinite(dependency))).map(String) : [],
  })) : [];
  return {
    ...payload,
    id: runId,
    goal: safeText(payload.goal, "Engineering plan"),
    status: RUN_STATUSES.has(payload.status) ? payload.status : "unknown",
    workspace: safeScalar(payload.workspace),
    workspace_revision: safeScalar(payload.workspace_revision),
    execution_state: safeText(payload.execution_state),
    error: safeText(payload.error),
    tasks,
  };
}

export function resolveWorkspaceId(workspaces, candidate) {
  const available = Array.isArray(workspaces)
    ? workspaces.filter((workspace) => workspace && (typeof workspace.id === "string" && workspace.id.trim() || typeof workspace.id === "number" && Number.isFinite(workspace.id)))
    : [];
  const requested = candidate === null || candidate === undefined ? "" : String(candidate);
  if (requested && available.some((workspace) => String(workspace.id) === requested)) return requested;
  return available.length ? String(available[0].id) : "";
}

export function normalizeWorkspaces(payload) {
  const rows = Array.isArray(payload) ? payload : Array.isArray(payload?.results) ? payload.results : [];
  const seen = new Set();
  return rows.flatMap((workspace) => {
    if (!workspace || typeof workspace !== "object" || Array.isArray(workspace)) return [];
    const id = typeof workspace.id === "number" ? workspace.id
      : typeof workspace.id === "string" && /^\d+$/.test(workspace.id.trim()) ? Number(workspace.id.trim()) : NaN;
    if (!Number.isSafeInteger(id) || id < 1 || seen.has(id)) return [];
    seen.add(id);
    return [{ id, name: typeof workspace.name === "string" && workspace.name.trim() ? workspace.name.trim() : `Workspace #${id}` }];
  });
}
