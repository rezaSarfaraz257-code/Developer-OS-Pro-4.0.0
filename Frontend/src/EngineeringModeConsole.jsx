import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, formatApiError } from "./services/api";
import "./EngineeringModeConsole.css";

const ACTIVE_STATUSES = new Set(["queued", "planning"]);
const STATUS_LABELS = {
  queued: "Queued",
  planning: "Planning",
  awaiting_approval: "Awaiting approval",
  approved: "Plan approved",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  canceled: "Canceled",
};

function responseError(payload, fallback) {
  return formatApiError(payload, fallback);
}

export default function EngineeringModeConsole({ go }) {
  const [mode, setMode] = useState("manual");
  const [workspaces, setWorkspaces] = useState([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [goal, setGoal] = useState("");
  const [run, setRun] = useState(null);
  const [loadingWorkspaces, setLoadingWorkspaces] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pendingRequestRef = useRef(null);

  const refreshRun = useCallback(async (id, { quiet = false } = {}) => {
    if (!id) return null;
    try {
      const response = await apiFetch(`/ai/agent/runs/${id}/`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(responseError(payload, "Agent run status is unavailable."));
      setRun(payload);
      setError("");
      return payload;
    } catch (requestError) {
      if (!quiet) setError(requestError?.message || "Could not refresh agent status.");
      return null;
    }
  }, []);

  useEffect(() => {
    let active = true;
    apiFetch("/ide/workspaces/")
      .then(async (response) => {
        const payload = await response.json().catch(() => []);
        if (!response.ok) throw new Error(responseError(payload, "Workspaces could not be loaded."));
        return Array.isArray(payload) ? payload : Array.isArray(payload?.results) ? payload.results : [];
      })
      .then((items) => {
        if (!active) return;
        setWorkspaces(items);
        // Select a safe default immediately; a restored run can then override it
        // with its own workspace once the owner-scoped detail request completes.
        if (items.length) setWorkspaceId((current) => current || String(items[0].id));
      })
      .catch((requestError) => {
        if (active) setError(requestError?.message || "Workspaces could not be loaded.");
      })
      .finally(() => {
        if (active) setLoadingWorkspaces(false);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    try {
      const savedId = window.sessionStorage.getItem("dos:last-agent-run");
      if (savedId) {
        refreshRun(savedId, { quiet: true }).then((payload) => {
          if (!active) return;
          if (payload?.workspace) {
            setWorkspaceId(String(payload.workspace));
          } else {
            try { window.sessionStorage.removeItem("dos:last-agent-run"); } catch { /* optional persistence */ }
          }
        });
      }
    } catch {
      // Session storage is an enhancement; the agent workflow remains usable without it.
    }
    return () => { active = false; };
  }, [refreshRun]);

  useEffect(() => {
    if (!run?.id || !ACTIVE_STATUSES.has(run.status)) return undefined;
    let active = true;
    let timer;
    const poll = async () => {
      const latest = await refreshRun(run.id, { quiet: true });
      if (active && (!latest || ACTIVE_STATUSES.has(latest.status))) {
        timer = window.setTimeout(poll, latest ? 2200 : 3500);
      }
    };
    timer = window.setTimeout(poll, 1100);
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [run?.id, run?.status, refreshRun]);

  const createPlan = async (event) => {
    event.preventDefault();
    const cleanGoal = goal.trim();
    if (!cleanGoal || !workspaceId || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const requestSignature = `${workspaceId}:${cleanGoal}`;
      let pendingRequest = pendingRequestRef.current;
      if (!pendingRequest || pendingRequest.signature !== requestSignature) {
        try {
          const stored = JSON.parse(window.sessionStorage.getItem("dos:pending-agent-request") || "null");
          if (stored?.signature === requestSignature && typeof stored.key === "string") pendingRequest = stored;
        } catch { /* storage is optional; in-memory idempotency still applies */ }
      }
      if (!pendingRequest || pendingRequest.signature !== requestSignature) {
        const key = globalThis.crypto?.randomUUID?.()
          || `agent-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
        pendingRequest = { signature: requestSignature, key };
      }
      pendingRequestRef.current = pendingRequest;
      try { window.sessionStorage.setItem("dos:pending-agent-request", JSON.stringify(pendingRequest)); } catch { /* optional retry persistence */ }
      const idempotencyKey = pendingRequest.key;
      const response = await apiFetch("/ai/agent/runs/", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({ goal: cleanGoal, workspace: Number(workspaceId), idempotency_key: idempotencyKey }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(responseError(payload, "The engineering plan could not be created."));
      setRun(payload);
      setGoal("");
      pendingRequestRef.current = null;
      try {
        window.sessionStorage.setItem("dos:last-agent-run", String(payload.id));
        window.sessionStorage.removeItem("dos:pending-agent-request");
      } catch { /* optional persistence */ }
      setNotice("Plan request accepted. Waiting for the planning worker.");
    } catch (requestError) {
      setError(requestError?.message || "The engineering plan could not be created.");
    } finally {
      setBusy(false);
    }
  };

  const approvePlan = async () => {
    if (!run?.id || busy || run.status !== "awaiting_approval") return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(`/ai/agent/runs/${run.id}/approve/`, { method: "POST", body: JSON.stringify({}) });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const approvalError = new Error(responseError(payload, "The plan could not be approved."));
        approvalError.code = payload?.code;
        throw approvalError;
      }
      setRun(payload);
      setNotice("Plan approval recorded. This approval does not execute tasks or modify workspace files.");
    } catch (requestError) {
      setError(requestError?.message || "The plan could not be approved.");
      if (requestError?.code === "stale_workspace") setNotice("The workspace changed after planning. Generate a fresh plan before approval.");
    } finally {
      setBusy(false);
    }
  };

  const cancelRun = async () => {
    if (!run?.id || busy || !ACTIVE_STATUSES.has(run.status)) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(`/ai/agent/runs/${run.id}/cancel/`, { method: "POST", body: JSON.stringify({}) });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(responseError(payload, "The agent run could not be canceled."));
      setRun(payload);
      setNotice("Agent planning run canceled.");
    } catch (requestError) {
      setError(requestError?.message || "The agent run could not be canceled.");
    } finally {
      setBusy(false);
    }
  };

  const status = run?.status || "idle";
  const openWorkspaceInIde = () => {
    try { if (workspaceId) window.sessionStorage.setItem("dos:requested-ide-workspace", String(workspaceId)); } catch { /* optional handoff */ }
    go("/ide");
  };
  const tasks = Array.isArray(run?.tasks) ? run.tasks : [];

  return (
    <section className="cc-engineering-modes" aria-labelledby="cc-engineering-modes-title">
      <div className="cc-engineering-modes-head">
        <div>
          <span className="cc-section-eyebrow">ENGINEERING WORKFLOW</span>
          <h2 id="cc-engineering-modes-title">Choose how to build</h2>
          <p>One workspace, three ways to move from intent to implementation.</p>
        </div>
        <span className="cc-mode-workspace-state"><i /> SHARED WORKSPACE</span>
      </div>

      <div className="cc-mode-grid" role="group" aria-label="Build mode">
        <button type="button" className={`cc-mode-card ${mode === "manual" ? "is-selected" : ""}`} aria-pressed={mode === "manual"} onClick={() => setMode("manual")}>
          <span className="cc-mode-icon">⌘</span><span className="cc-mode-number">01 / DIRECT CONTROL</span>
          <strong>Manual Build</strong><span>Work directly in the Web IDE with full control over files, editor and execution tools.</span>
          <em>Open Web IDE <b>↗</b></em>
        </button>
        <button type="button" className={`cc-mode-card cc-mode-agent ${mode === "agent" ? "is-selected" : ""}`} aria-pressed={mode === "agent"} onClick={() => setMode("agent")}>
          <span className="cc-mode-icon">✳</span><span className="cc-mode-number">02 / PLAN WITH AGENTS</span>
          <strong>Agent Build</strong><span>Describe the engineering goal and receive a reviewable, dependency-aware task plan.</span>
          <em>Create agent plan <b>→</b></em>
        </button>
        <button type="button" className={`cc-mode-card cc-mode-hybrid ${mode === "hybrid" ? "is-selected" : ""}`} aria-pressed={mode === "hybrid"} onClick={() => setMode("hybrid")}>
          <span className="cc-mode-icon">⇄</span><span className="cc-mode-number">03 / HUMAN + AI</span>
          <strong>Hybrid Build</strong><span>Use an agent plan for direction, then continue hands-on in the Web IDE. Select the same workspace in the IDE workspace selector.</span>
          <em>Plan + collaborate <b>→</b></em>
        </button>
      </div>

      {mode === "manual" ? (
        <div className="cc-mode-detail cc-mode-manual-detail">
          <div><span className="cc-mode-detail-label">MANUAL WORKSPACE</span><strong>Start with direct engineering control.</strong><p>Your files, editor, terminal, diagnostics and workspace history stay in the Web IDE.</p></div>
          <button type="button" className="cc-mode-primary" onClick={openWorkspaceInIde}>Open Web IDE <span>↗</span></button>
          {run && <div className="cc-manual-agent-signal"><span className="cc-mode-detail-label">LAST AGENT RUN · #{run.id}</span><span className={`cc-agent-status is-${status}`}><i />{STATUS_LABELS[status] || status}</span><strong>{run.goal}</strong><button type="button" onClick={() => setMode("agent")}>View agent plan →</button></div>}
        </div>
      ) : (
        <div className="cc-mode-detail cc-agent-detail">
          <div className="cc-agent-form-head">
            <div><span className="cc-mode-detail-label">{mode === "hybrid" ? "HYBRID WORKFLOW" : "AGENT PLANNING"}</span><strong>{mode === "hybrid" ? "Plan with an agent. Build with control." : "Turn an engineering goal into a reviewable plan."}</strong><p>{mode === "hybrid" ? "The agent prepares the task graph; you continue implementation in the same workspace." : "The current agent runtime creates and tracks plans. It does not yet execute code or mutate files."}</p></div>
            {run && <span className={`cc-agent-status is-${status}`}><i />{STATUS_LABELS[status] || status}</span>}
          </div>
          <form className="cc-agent-create-form" onSubmit={createPlan}>
            <label className="cc-agent-field"><span>WORKSPACE</span><select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} disabled={loadingWorkspaces || !workspaces.length}><option value="">{loadingWorkspaces ? "Loading workspaces…" : "Select a workspace"}</option>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name || `Workspace #${workspace.id}`}</option>)}</select></label>
            <label className="cc-agent-field cc-agent-goal"><span>ENGINEERING GOAL</span><textarea value={goal} onChange={(event) => setGoal(event.target.value)} maxLength={12000} rows={3} placeholder="Example: Review the project architecture, identify the highest-risk frontend issues, and prepare a safe implementation plan." /></label>
            <div className="cc-agent-form-actions"><small>{goal.length.toLocaleString()} / 12,000 characters</small><button type="submit" className="cc-mode-primary" disabled={busy || loadingWorkspaces || !workspaceId || !goal.trim()}>{busy ? "Submitting…" : "Generate plan"} <span>✳</span></button></div>
          </form>
          {!loadingWorkspaces && !workspaces.length && <div className="cc-agent-message">No workspace is available yet. Create one in the Web IDE before requesting an agent plan. <button type="button" className="cc-agent-inline-link" onClick={() => go("/ide")}>Open Web IDE ↗</button></div>}
          {error && <p className="cc-agent-message is-error" role="alert">{error}</p>}
          {notice && <p className="cc-agent-message" role="status">{notice}</p>}
          {run && (
            <div className="cc-agent-run" aria-live="polite">
              <div className="cc-agent-run-head"><div><span className="cc-mode-detail-label">LIVE AGENT RUN · #{run.id}</span><strong>{run.goal}</strong><small>Workspace revision {run.workspace_revision} · {run.execution_state === "plan_only_no_workspace_mutations" ? "Plan-only · no file mutations" : "Execution state reported by server"}</small></div><div className="cc-agent-run-actions"><button type="button" onClick={() => refreshRun(run.id)} disabled={busy}>Refresh</button>{ACTIVE_STATUSES.has(run.status) && <button type="button" onClick={cancelRun} disabled={busy}>Cancel</button>}{run.status === "awaiting_approval" && <button type="button" className="cc-mode-primary" onClick={approvePlan} disabled={busy}>Review & approve</button>}{run.status === "approved" && mode === "hybrid" && <button type="button" className="cc-mode-primary" onClick={openWorkspaceInIde}>Continue in IDE <span>↗</span></button>}</div></div>
              {run.error && <p className="cc-agent-message is-error" role="alert">{run.error}</p>}
              {tasks.length > 0 ? <ol className="cc-agent-task-list">{tasks.map((task) => <li key={task.id}><span className={`cc-agent-task-state is-${task.status}`} /><div><strong>{task.title}</strong><small>{task.agent_role.replaceAll("_", " ")} · {task.status}{task.depends_on?.length ? ` · depends on ${task.depends_on.join(", ")}` : ""}</small></div></li>)}</ol> : <p className="cc-agent-empty">{ACTIVE_STATUSES.has(run.status) ? "The planning worker is preparing the task graph…" : "No task graph has been returned for this run."}</p>}
              {run.status === "approved" && <p className="cc-agent-message">Approval is recorded. Task execution remains unavailable in this runtime; no work is represented as completed.</p>}
            </div>
          )}
          {mode === "hybrid" && run?.status === "approved" && <div className="cc-hybrid-next"><span>HUMAN-LED IMPLEMENTATION</span><button type="button" onClick={openWorkspaceInIde}>Open Web IDE · selected workspace ↗</button></div>}
        </div>
      )}
    </section>
  );
}
