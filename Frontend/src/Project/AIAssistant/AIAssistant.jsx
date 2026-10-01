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
  return data?.error || data?.detail || fallback;
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
    try {
      const response = await apiFetch("/ai/chat/", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          workspace: workspace || undefined,
          conversation: conversation?.id || undefined,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorText(data, "AI request failed."));
      setConversation(data.conversation || null);
      setAnswer(data.message?.content || data.answer || "The intelligence engine returned no answer.");
      setMode(data.provider_status ? "local fallback" : data.message ? "workspace intelligence" : "assistant");
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
            <div className="ai-badge">✦ CONTEXT-AWARE ENGINE</div>
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
              <button className="primary" disabled={busy || !message.trim()}>{busy ? "ANALYZING…" : "ASK INTELLIGENCE →"}</button>
            </div>
          </form>
          <div className="ai-actions ai-actions-pro">
            {QUICK_ACTIONS.map(([id, label, hint]) => <button type="button" key={id} onClick={() => runAction(id)} disabled={busy || !message.trim()} title={hint}>{label}</button>)}
          </div>
          {error && <div className="ai-error">{error}</div>}
        </section>

        <aside className="ai-card ai-side-card">
          <div className="ai-side-title"><span>QUICK START</span><b>⌘</b></div>
          {["Review my current workspace", "Find the biggest delivery risk", "Explain this error", "Plan my next milestone"].map((prompt) => (
            <button className="prompt" key={prompt} onClick={() => setMessage(prompt)}>{prompt}<span>→</span></button>
          ))}
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
