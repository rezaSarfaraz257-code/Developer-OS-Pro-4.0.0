import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../../services/api";
import "./AIAssistant.css";

const QUICK_ACTIONS = [
  ["review", "Review code", "Find bugs, security issues and maintainability risks."],
  ["tests", "Generate tests", "Create focused tests and edge cases."],
  ["debug", "Debug error", "Trace the likely root cause and safest fix."],
  ["plan", "Plan next", "Turn the request into small implementation steps."],
  ["explain", "Explain", "Explain the architecture or code clearly."],
];

function errorText(data, fallback) {
  const error = data?.error;
  if (typeof error === "string" && error.trim()) return error;
  if (error && typeof error === "object") {
    const message = [error.message, error.detail].filter(Boolean).join(" · ");
    if (message) return message;
  }
  return data?.detail || fallback;
}

export default function AIAssistantPage({ setPage }) {
  const [message, setMessage] = useState("");
  const [answer, setAnswer] = useState("");
  const [mode, setMode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [workspaces, setWorkspaces] = useState([]);
  const [workspace, setWorkspace] = useState("");
  const [conversation, setConversation] = useState(null);
  const [usage, setUsage] = useState(null);
  const [agentMode, setAgentMode] = useState(false);
  const [evidence, setEvidence] = useState(null);
  const [approvalToken, setApprovalToken] = useState(null);
  const [applyResult, setApplyResult] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [toolCalls, setToolCalls] = useState([]);
  const [showDiff, setShowDiff] = useState(true);
  const [agentRun, setAgentRun] = useState(null);
  const [agentToolResult, setAgentToolResult] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiFetch("/ide/workspaces/").then(async (r) => ({ ok: r.ok, data: await r.json().catch(() => []) })),
      apiFetch("/usage/").then(async (r) => ({ ok: r.ok, data: await r.json().catch(() => null) })),
    ]).then(([ws, meter]) => {
      if (cancelled) return;
      setWorkspaces(ws.ok && Array.isArray(ws.data) ? ws.data : []);
      if (ws.ok && Array.isArray(ws.data) && ws.data[0]) setWorkspace(String(ws.data[0].id));
      if (meter.ok) setUsage(meter.data);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const activeWorkspace = useMemo(
    () => workspaces.find((item) => String(item.id) === String(workspace)),
    [workspaces, workspace],
  );

  const syncJobId = applyResult?.sync?.job_id;
  const syncJobStatus = applyResult?.sync?.status;

  const refreshAgentRun = useCallback(async (runId = agentRun?.id) => {
    if (!runId) return;
    const response = await apiFetch(`/ai/agent/runs/${runId}/`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorText(data, "Unable to refresh Agent run."));
    setAgentRun(data);
    setMode(`AGENT RUN · ${String(data.status || "unknown").replaceAll("_", " ").toUpperCase()}`);
    const terminalError = ["failed", "canceled"].includes(data.status);
    setTimeline([
      { label: "PLAN", state: data.plan ? "complete" : terminalError ? "error" : "active" },
      { label: "HUMAN APPROVAL", state: data.status === "awaiting_approval" ? "ready" : ["approved", "running", "succeeded"].includes(data.status) ? "complete" : terminalError ? "error" : "pending" },
      { label: "ENGINEERING TASKS", state: data.status === "succeeded" ? "complete" : ["approved", "running"].includes(data.status) ? "active" : terminalError ? "error" : "pending" },
    ]);
  }, [agentRun?.id]);

  async function approveAgentRun() {
    if (!agentRun?.id || busy) return;
    setBusy(true); setError("");
    try {
      const response = await apiFetch(`/ai/agent/runs/${agentRun.id}/approve/`, { method: "POST", body: JSON.stringify({}) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorText(data, "Agent plan approval failed."));
      setAgentRun(data);
      await refreshAgentRun(data.id);
    } catch (err) {
      setError(err.message || "Agent plan approval failed.");
    } finally {
      setBusy(false);
    }
  }

  async function cancelAgentRun() {
    if (!agentRun?.id || busy) return;
    setBusy(true); setError("");
    try {
      const response = await apiFetch(`/ai/agent/runs/${agentRun.id}/cancel/`, { method: "POST", body: JSON.stringify({}) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorText(data, "Agent cancellation failed."));
      setAgentRun(data);
      await refreshAgentRun(data.id);
    } catch (err) {
      setError(err.message || "Agent cancellation failed.");
    } finally {
      setBusy(false);
    }
  }

  async function invokeAgentTool(task, tool) {
    if (!agentRun?.id || busy) return;
    setBusy(true); setError("");
    try {
      const response = await apiFetch(`/ai/agent/runs/${agentRun.id}/tasks/${task.id}/tools/${tool}/`, {
        method: "POST", body: JSON.stringify({}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorText(data, "Agent evidence tool failed."));
      await refreshAgentRun(agentRun.id);
      setToolCalls((old) => [...old, { name: tool, state: "complete" }]);
      setAgentToolResult(JSON.stringify(data.result || {}, null, 2).slice(0, 12000));
      setAnswer(`Task "${task.title}" completed using ${tool}. The tool is read-only; no workspace files were changed.`);
    } catch (err) {
      setError(err.message || "Agent evidence tool failed.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!agentRun?.id || !["queued", "planning"].includes(agentRun.status)) return undefined;
    const timer = setInterval(() => {
      refreshAgentRun(agentRun.id).catch((err) => setError(err.message || "Agent status refresh failed."));
    }, 4000);
    return () => clearInterval(timer);
  }, [agentRun?.id, agentRun?.status, refreshAgentRun]);

  useEffect(() => {
    const jobId = syncJobId;
    if (!jobId || !["queued", "running"].includes(syncJobStatus)) return undefined;

    let active = true;
    const refreshSyncStatus = async () => {
      try {
        const response = await apiFetch(`/ai/agent/sync-jobs/${jobId}/`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorText(data, "Runner sync status is unavailable."));
        if (!active) return;
        setApplyResult((current) => {
          if (!current || current.sync?.job_id !== jobId) return current;
          return {
            ...current,
            sync: {
              ...current.sync,
              status: data.status,
              ok: data.status === "succeeded",
              attempts: data.attempts,
              revision: data.revision,
              coalesced: data.coalesced,
              error: data.error || "",
            },
          };
        });
        if (data.status === "succeeded") setMode("PATCH SAVED · RUNNER SYNCED");
        if (data.status === "failed") {
          setMode("PATCH SAVED · RUNNER SYNC FAILED");
          setError(data.error || "Runner synchronization failed.");
        }
      } catch (err) {
        if (active) setError(err.message || "Runner sync status refresh failed.");
      }
    };

    refreshSyncStatus();
    const timer = setInterval(refreshSyncStatus, 4000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [syncJobId, syncJobStatus]);

  async function ask(e) {
    e?.preventDefault();
    const text = message.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    if (agentMode) { setTimeline([{label:"ANALYZE",state:"active"},{label:"INSPECT",state:"active"},{label:"DIAGNOSE",state:"active"}]); setToolCalls([]); }
    try {
      if (agentMode) {
        const response = await apiFetch("/ai/agent/runs/", {
          method: "POST",
          body: JSON.stringify({
            goal: text,
            workspace: workspace || undefined,
            conversation: conversation?.id || undefined,
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(`${errorText(data, "Unable to start Agent workflow.")} · HTTP ${response.status}`);
        setAgentRun(data); setAgentToolResult("");
        if (data.workspace) setWorkspace(String(data.workspace));
        setConversation(data.conversation ? { id: data.conversation } : conversation);
        setEvidence(null); setApprovalToken(null); setApplyResult(null); setToolCalls([]);
        setAnswer(data.status === "awaiting_approval" ? "A reviewable engineering plan is ready. Inspect the tasks and approve before any specialist tool can run." : "Your engineering plan is being prepared. This workflow does not modify workspace files.");
        setMode(`AGENT RUN · ${String(data.status || "queued").toUpperCase()}`);
        setTimeline([{ label: "PLAN", state: "active" }, { label: "HUMAN APPROVAL", state: "pending" }, { label: "ENGINEERING TASKS", state: "pending" }]);
        setMessage("");
        return;
      }
      const response = await apiFetch("/ai/chat/", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          workspace: workspace || undefined,
          conversation: conversation?.id || undefined,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = errorText(data, "AI request failed.");
        // If the deployed backend still returns the legacy preparation error,
        // run the non-billable readiness check once and expose the actual stage.
        if (!agentMode && response.status >= 500 && message.includes("could not prepare")) {
          try {
            const healthResponse = await apiFetch("/ai/health/");
            const health = await healthResponse.json().catch(() => ({}));
            const checks = health?.checks ? Object.entries(health.checks).map(([key, value]) => `${key}=${value ? "ok" : "failed"}`).join(" · ") : "";
            const provider = health?.details?.provider;
            const providerInfo = provider ? ` · model=${provider.model || "unknown"} · protocol=${provider.protocol || "unknown"}` : "";
            throw new Error(`${message} · readiness: ${health.status || "unavailable"}${checks ? ` · ${checks}` : ""}${providerInfo}`);
          } catch (healthError) {
            if (healthError?.message && healthError.message !== message) throw healthError;
          }
        }
        throw new Error(`${message} · HTTP ${response.status}`);
      }
      setConversation(data.conversation || null);
      setEvidence(data.evidence || null);
      if (agentMode) { setToolCalls(data.tool_calls || []); setTimeline((items) => items.map((x) => ({...x,state:"complete"})).concat(data.approval_token ? [{label:"PATCH PROPOSAL",state:"ready"}] : [{label:"VERIFY",state:"complete"}])); }
      setApprovalToken(data.approval_token || null);
      setApplyResult(null);
      setAnswer(data.message?.content || data.answer || "The intelligence engine returned no answer.");
      setMode(agentMode ? (data.mode || "agentic intelligence") : (data.mode === "fallback" ? "local fallback" : "workspace intelligence"));
      setMessage("");
      if (data.usage) setUsage((old) => old ? { ...old, metrics: { ...old.metrics, ai_messages: { ...old.metrics?.ai_messages, used: data.usage.used } } } : old);
    } catch (err) {
      setError(err.message || "AI request failed.");
    } finally {
      setBusy(false);
    }
  }

  async function runAction(action) {
    const text = message.trim();
    if (!text || busy) return ask();
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch("/ai/actions/", {
        method: "POST",
        body: JSON.stringify({ action, input: text, workspace: workspace || undefined }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorText(data, "AI action failed."));
      setAnswer(data.answer || "No result returned.");
      setMode(`${action} · ${data.mode || "workspace intelligence"}`);
      setMessage("");
    } catch (err) {
      setError(err.message || "AI action failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="ai-page ai-assistant-pro">
      <div className="ai-head">
        <div>
          <span>DEVELOPER OS / INTELLIGENCE</span>
          <h2>Developer Intelligence</h2>
          <p>Ask questions against your real projects, tasks, notes, snippets and selected IDE workspace.</p>
        </div>
        <div className="ai-head-actions">
          <select value={workspace} onChange={(e) => setWorkspace(e.target.value)} aria-label="AI workspace">
            <option value="">All workspace context</option>
            {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          <button onClick={() => setPage("dashboard")}>Back to workspace</button>
        </div>
      </div>

      <div className="ai-layout ai-layout-pro">
        <section className="ai-card ai-main-card">
          <div className="ai-status-row">
            <div className="ai-badge">✦ {agentMode ? "AGENTIC DEVELOPER INTELLIGENCE" : "CONTEXT-AWARE ENGINE"}</div>
            <span className={mode === "local fallback" ? "ai-fallback" : "ai-online"}>{busy ? "THINKING…" : mode || "READY"}</span>
          </div>
          {answer ? (
            <div className="ai-answer">
              <small>{mode || "Developer OS Intelligence"}</small>
              <div>{answer}</div>
            </div>
          ) : (
            <div className="ai-empty ai-empty-pro">
              <strong>What should we solve?</strong>
              <span>Paste an error, describe a feature, or ask for an architecture/code review. The selected workspace is included as structured context.</span>
            </div>
          )}
          <form onSubmit={ask} className="ai-composer">
            <textarea value={message} onChange={(e) => setMessage(e.target.value)} disabled={busy} placeholder="Ask Developer OS Intelligence…" />
            <div className="ai-composer-bottom">
              <label className="ai-workspace-picker"><span>WORKSPACE</span><select value={workspace} onChange={(event) => setWorkspace(event.target.value)} disabled={busy}><option value="">Select workspace…</option>{workspaces.map((item) => <option key={item.id} value={String(item.id)}>{item.name}</option>)}</select></label>
              <label className="ai-agent-toggle"><input type="checkbox" checked={agentMode} onChange={(e) => setAgentMode(e.target.checked)} /> Agent mode</label>
              <button className="primary" disabled={busy || !message.trim() || (agentMode && !workspace)}>{busy ? "ANALYZING…" : "ASK INTELLIGENCE →"}</button>
            </div>
          </form>
          <div className="ai-actions ai-actions-pro">
            {QUICK_ACTIONS.map(([id, label, hint]) => <button type="button" key={id} onClick={() => runAction(id)} disabled={busy || !message.trim()} title={hint}>{label}</button>)}
          </div>

          {agentMode && agentRun && (
            <section className="agent-run-panel" aria-live="polite">
              <div className="agent-run-heading">
                <div><small>AUTONOMOUS ENGINEERING RUN</small><strong>Run #{agentRun.id} · {String(agentRun.status).replaceAll("_", " ").toUpperCase()}</strong></div>
                <div className="agent-run-actions"><button type="button" disabled={busy} onClick={() => refreshAgentRun().catch((err) => setError(err.message))}>REFRESH</button>{!["succeeded", "failed", "canceled"].includes(agentRun.status) && <button type="button" disabled={busy} onClick={cancelAgentRun}>CANCEL RUN</button>}</div>
              </div>
              <p>{agentRun.plan?.summary || agentRun.goal}</p>
              <div className="agent-run-context"><span>WORKSPACE ID <b>#{agentRun.workspace}</b></span><span>REVISION <b>{agentRun.workspace_revision}</b></span><span>EXECUTION <b>PLAN ONLY · NO FILE CHANGES</b></span></div>
              {agentRun.plan?.acceptance_criteria?.length > 0 && <div className="agent-run-criteria"><b>ACCEPTANCE CRITERIA</b>{agentRun.plan.acceptance_criteria.map((item, index) => <span key={index}>{index + 1}. {item}</span>)}</div>}
              {agentRun.error && <div className="agent-run-error">{agentRun.error}</div>}
              {agentRun.plan?.risks?.length > 0 && <div className="agent-run-risks"><b>RISKS TO REVIEW</b>{agentRun.plan.risks.map((risk, index) => <span key={index}>{risk}</span>)}</div>}
              <div className="agent-run-tasks">
                {(agentRun.tasks || []).map((task) => (
                  <article className="agent-run-task" key={task.id}>
                    <div className="agent-run-task-title"><b>{task.sequence + 1}. {task.title}</b><span>{task.status.replaceAll("_", " ")}</span></div>
                    <p>{task.description}</p>
                    <small>{task.agent_role.replaceAll("_", " ")} · {task.depends_on?.length ? `Depends on: ${task.depends_on.join(", ")}` : "No dependencies"}</small>
                    {["approved", "running"].includes(agentRun.status) && task.status === "ready" && task.allowed_capabilities?.length > 0 && (
                      <div className="agent-run-tools">{task.allowed_capabilities.map((tool) => <button type="button" key={tool} disabled={busy} onClick={() => invokeAgentTool(task, tool)}>{tool.replaceAll("_", " ")}</button>)}</div>
                    )}
                  </article>
                ))}
              </div>
              {agentRun.status === "awaiting_approval" && <button type="button" className="primary agent-run-approve" disabled={busy} onClick={approveAgentRun}>APPROVE PLAN & UNLOCK TOOLS →</button>}
              {agentToolResult && <div className="agent-run-result"><b>LATEST TOOL EVIDENCE</b><pre>{agentToolResult}</pre></div>}
              <small className="agent-run-safety">Human approval is required. Available specialist tools are read-only and cannot execute shell commands or mutate workspace files.</small>
            </section>
          )}
          {agentMode && <div className="agent-control-center">
            <div className="acc-header"><div><small>AGENT CONTROL CENTER</small><strong>ENGINEERING LOOP</strong></div><span>{busy ? "LIVE · WORKING" : "READY · OBSERVABLE"}</span></div>
            <div className="acc-timeline">{timeline.length ? timeline.map((item,index) => <div className={"acc-step "+item.state} key={index}><i></i><span>{item.label}</span></div>) : <div className="acc-muted">Start an Agent request to initialize the engineering loop.</div>}</div>
            <div className="acc-tools"><div className="acc-section-title">TOOL CALLS <b>{toolCalls.length}</b></div>{toolCalls.length ? toolCalls.map((call,index) => <div className="acc-tool" key={index}><span>●</span><b>{call.name || call.tool || "tool"}</b><em>{call.state || "complete"}</em></div>) : <div className="acc-muted">Tool activity will appear here when the Agent invokes evidence tools.</div>}</div>
            {agentMode && <div className="acc-intel-grid"><div><small>CONFIDENCE</small><b>LIVE</b></div><div><small>RISK</small><b>REVIEW</b></div><div><small>LOOP</small><b>{applyResult ? "VERIFYING" : approvalToken ? "APPROVAL" : busy ? "RUNNING" : "READY"}</b></div></div>}
            {evidence?.repository && <div className="acc-metrics"><div><small>REPOSITORY</small><b>{evidence.repository.connected ? "CONNECTED" : "OFFLINE"}</b></div><div><small>DIAGNOSTICS</small><b>{evidence.diagnostics?.status || "UNKNOWN"}</b></div><div><small>RUNNER</small><b>{evidence.runner?.status || "UNKNOWN"}</b></div></div>}
            {answer && answer.trim().startsWith("{") && <div className="acc-proposal"><div className="acc-section-title">PATCH PREVIEW <button onClick={() => setShowDiff(!showDiff)}>{showDiff ? "HIDE" : "SHOW"}</button></div>{showDiff && <pre>{answer}</pre>}</div>}
          </div>}
          {approvalToken && (
            <div className="ai-approval-panel">
              <div><strong>PATCH PROPOSAL READY</strong><span>Review the generated change before applying it to your workspace.</span></div>
              <button type="button" className="primary" disabled={busy} onClick={async () => {
                setBusy(true); setError(""); setApplyResult(null);
                try {
                  const response = await apiFetch("/ai/agent/apply/", { method: "POST", body: JSON.stringify({ approval_token: approvalToken }) });
                  const data = await response.json().catch(() => ({}));
                  if (!response.ok) throw new Error(errorText(data, "Patch application failed."));
                  setApplyResult(data); setApprovalToken(null); setMode(data.status === "applied_sync_pending" ? "PATCH SAVED · RUNNER SYNC QUEUED" : "PATCH APPLIED · VERIFYING");
                } catch (err) { setError(err.message || "Patch application failed."); }
                finally { setBusy(false); }
              }}>APPROVE & APPLY →</button>
              <button type="button" disabled={busy} onClick={() => setApprovalToken(null)}>REJECT</button>
            </div>
          )}
          {applyResult && (
            <div className="ai-approval-result">
              <strong>{applyResult.status === "applied_sync_pending" && applyResult.sync?.status === "succeeded" ? "WORKSPACE UPDATED · RUNNER SYNCED" : applyResult.status === "applied_sync_pending" && applyResult.sync?.status === "failed" ? "WORKSPACE SAVED · RUNNER SYNC FAILED" : applyResult.status === "applied_sync_pending" && applyResult.sync?.status === "running" ? "WORKSPACE UPDATED · RUNNER SYNCING" : applyResult.status === "applied_sync_pending" ? "WORKSPACE UPDATED · RUNNER SYNC QUEUED" : applyResult.repair_loop?.passed ? "VERIFICATION PASSED" : "PATCH RESULT · VERIFICATION PENDING"}</strong>
              <span>{applyResult.changed_files?.length || 0} file(s) changed · Runner sync: {applyResult.sync?.status || applyResult.verification?.status || "unknown"} · Diagnostics: {applyResult.diagnostics?.status || "unknown"}</span>
              <small>{applyResult.status === "applied_sync_pending" && applyResult.sync?.status === "succeeded" ? "The saved workspace revision has reached the Runner. This confirms synchronization only; diagnostics and execution verification are still pending." : applyResult.status === "applied_sync_pending" && applyResult.sync?.status === "failed" ? "The workspace change remains saved, but Runner synchronization failed. Do not treat this patch as executable or verified." : applyResult.status === "applied_sync_pending" ? "The workspace change is saved. Runner synchronization is being tracked automatically; diagnostics and execution verification are not complete yet." : applyResult.repair_loop?.next_step === "complete" ? "Verification passed — repair loop complete." : applyResult.repair_loop?.next_step === "manual_review" ? "Iteration limit reached — manual review required." : "Verification feedback is pending. Review the Runner and diagnostics status before treating this change as complete."}</small>
            </div>
          )}
          {error && <div className="ai-error">{error}</div>}
        </section>

        <aside className="ai-card ai-side-card">
          <div className="ai-side-title"><span>QUICK START</span><b>⌘</b></div>
          {["Review my current workspace", "Find the biggest delivery risk", "Explain this error", "Plan my next milestone"].map((prompt) => (
            <button className="prompt" key={prompt} onClick={() => setMessage(prompt)}>{prompt}<span>→</span></button>
          ))}
          {agentMode && evidence && <div className="ai-context-box"><small>AGENT EVIDENCE</small><b>IDE · Repository · Diagnostics · Runner</b><span>Repo {evidence.repository?.connected ? "connected" : "not connected"} · Diagnostics {evidence.diagnostics?.status || "unknown"} · Runner {evidence.runner?.status || "unknown"}</span></div>}
          <div className="ai-context-box">
            <small>ACTIVE CONTEXT</small>
            <b>{activeWorkspace?.name || "All accessible workspaces"}</b>
            <span>{activeWorkspace ? `${Object.keys(activeWorkspace.files || {}).length} files · ${activeWorkspace.framework || "polyglot"}` : "Project-level intelligence"}</span>
          </div>
          {usage?.metrics?.ai_messages && <div className="ai-usage"><small>AI USAGE</small><b>{usage.metrics.ai_messages.used} / {usage.metrics.ai_messages.limit}</b><span>messages this month</span></div>}
        </aside>
      </div>
    </main>
  );
}
