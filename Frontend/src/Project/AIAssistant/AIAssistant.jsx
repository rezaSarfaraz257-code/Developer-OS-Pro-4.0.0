import { useEffect, useMemo, useState } from "react";
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

  async function ask(e) {
    e?.preventDefault();
    const text = message.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    if (agentMode) { setTimeline([{label:"ANALYZE",state:"active"},{label:"INSPECT",state:"active"},{label:"DIAGNOSE",state:"active"}]); setToolCalls([]); }
    try {
      const response = await apiFetch(agentMode ? "/ai/agent/" : "/ai/chat/", {
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
              <span>{activeWorkspace ? `Workspace: ${activeWorkspace.name}` : "Context: projects + tasks + notes + snippets"}</span>
              <label className="ai-agent-toggle"><input type="checkbox" checked={agentMode} onChange={(e) => setAgentMode(e.target.checked)} /> Agent mode</label>
              <button className="primary" disabled={busy || !message.trim()}>{busy ? "ANALYZING…" : "ASK INTELLIGENCE →"}</button>
            </div>
          </form>
          <div className="ai-actions ai-actions-pro">
            {QUICK_ACTIONS.map(([id, label, hint]) => <button type="button" key={id} onClick={() => runAction(id)} disabled={busy || !message.trim()} title={hint}>{label}</button>)}
          </div>
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
                  setApplyResult(data); setApprovalToken(null); setMode("PATCH APPLIED · VERIFYING");
                } catch (err) { setError(err.message || "Patch application failed."); }
                finally { setBusy(false); }
              }}>APPROVE & APPLY →</button>
              <button type="button" disabled={busy} onClick={() => setApprovalToken(null)}>REJECT</button>
            </div>
          )}
          {applyResult && (
            <div className="ai-approval-result">
              <strong>VERIFICATION COMPLETE</strong>
              <span>{applyResult.changed_files?.length || 0} file(s) changed · Runner: {applyResult.verification?.status || "unknown"} · Diagnostics: {applyResult.diagnostics?.status || "unknown"}</span>
              <small>{applyResult.repair_loop?.next_step === "complete" ? "Verification passed — repair loop complete." : applyResult.repair_loop?.next_step === "manual_review" ? "Iteration limit reached — manual review required." : "Verification feedback captured — re-analysis ready."}</small>
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
