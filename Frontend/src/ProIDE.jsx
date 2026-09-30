import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "./services/api";
import MonacoEditor from "./MonacoEditor";
import "./ProIDE.css";

const extLanguage = (path = "") => {
  const ext = (path.split(".").pop() || "").toLowerCase();
  return ({ js: "JavaScript", jsx: "React JSX", ts: "TypeScript", tsx: "React TSX", py: "Python", html: "HTML", css: "CSS", json: "JSON", md: "Markdown", yml: "YAML", yaml: "YAML", sql: "SQL", sh: "Shell", env: "Env", go: "Go", rs: "Rust", java: "Java", php: "PHP" }[ext] || "Text");
};

const iconFor = (path = "") => path.endsWith(".json") ? "{}" : path.endsWith(".py") ? "Py" : /\.(jsx|tsx)$/.test(path) ? "⚛" : path.endsWith(".css") ? "#" : "·";

function Tree({ files, active, onOpen }) {
  const paths = Object.keys(files).sort((a, b) => a.localeCompare(b));
  const roots = [];
  const seen = new Set();
  paths.forEach((path) => {
    const root = path.split("/")[0];
    if (!seen.has(root)) { seen.add(root); roots.push(root); }
  });
  return <div className="ide-tree">
    <div className="ide-tree-head">EXPLORER <span>{paths.length} files</span></div>
    <div className="ide-tree-list">
      {roots.map((root) => {
        const children = paths.filter((path) => path === root || path.startsWith(`${root}/`));
        const isDir = children.some((path) => path !== root);
        return isDir ? <div key={root} className="ide-folder-group">
          <div className="ide-folder">⌄ <span>/{root}</span></div>
          {children.map((path) => <button key={path} className={`ide-file ${path === active ? "active" : ""}`} onClick={() => onOpen(path)}>
            <span className="file-icon">{iconFor(path)}</span><span>{path.slice(root.length + 1)}</span>
          </button>)}
        </div> : <button key={root} className={`ide-file ${root === active ? "active" : ""}`} onClick={() => onOpen(root)}>
          <span className="file-icon">{iconFor(root)}</span><span>{root}</span>
        </button>;
      })}
      {!paths.length && <div className="ide-empty">No files yet.</div>}
    </div>
  </div>;
}

export default function ProIDE({ projectId, message }) {
  const [workspaces, setWorkspaces] = useState([]);
  const [ws, setWs] = useState(null);
  const [files, setFiles] = useState({});
  const [active, setActive] = useState("");
  const [frameworks, setFrameworks] = useState([]);
  const [framework, setFramework] = useState("react-vite");
  const [packages, setPackages] = useState("");
  const [terminal, setTerminal] = useState("");
  const [command, setCommand] = useState("");
  const [commandHistory, setCommandHistory] = useState([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [status, setStatus] = useState("Ready");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [showTerminal, setShowTerminal] = useState(true);
  const [quickOpen, setQuickOpen] = useState("");
  const [findText, setFindText] = useState("");
  const [cursor, setCursor] = useState({ line: 1, column: 1 });
  const editorRef = useRef(null);
  const autosaveTimer = useRef(null);
  const filesRef = useRef(files);
  const wsRef = useRef(ws);
  const activeRef = useRef(active);
  filesRef.current = files;
  wsRef.current = ws;
  activeRef.current = active;

  useEffect(() => {
    let cancelled = false;
    Promise.all([apiFetch("/ide/workspaces/"), apiFetch("/ide/frameworks/")])
      .then(async ([a, b]) => {
        const [workspaceData, frameworkData] = await Promise.all([a.json(), b.json()]);
        if (cancelled) return;
        setWorkspaces(workspaceData);
        setFrameworks(frameworkData);
      })
      .catch((e) => { if (!cancelled) message?.(e.message); });
    return () => { cancelled = true; };
  }, [message]);

  useEffect(() => {
    if (!ws && workspaces[0]) openWorkspace(workspaces[0]);
  }, [workspaces, ws]);

  useEffect(() => () => clearTimeout(autosaveTimer.current), []);

  async function openWorkspace(item) {
    if (!item) return;
    if (dirty && ws?.id) await saveFile(true);
    clearTimeout(autosaveTimer.current);
    setWs(item);
    setFiles(item.files || {});
    setActive(item.active_file || Object.keys(item.files || {})[0] || "");
    setDirty(false);
    setStatus("Workspace loaded");
  }

  async function createWorkspace() {
    const name = window.prompt("Workspace name", "My Developer OS App");
    if (!name?.trim()) return;
    try {
      const res = await apiFetch("/ide/workspaces/", { method: "POST", body: JSON.stringify({
        name: name.trim(), project: projectId || null, framework: "", runtime: "node", package_manager: "npm",
        files: { "README.md": `# ${name.trim()}\n\nBuilt with Developer OS.\n` },
      }) });
      const item = await res.json();
      setWorkspaces((x) => [item, ...x]);
      await openWorkspace(item);
    } catch (e) { setStatus(e.message); }
  }

  function updateContent(value) {
    if (!active) return;
    setFiles((x) => ({ ...x, [active]: value }));
    setDirty(true);
    setStatus("Unsaved changes · autosave scheduled");
    clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(() => void saveFile(true), 1200);
    updateCursor(editorRef.current);
  }

  function updateCursor(target) {
    if (!target) return;
    const pos = target.selectionStart || 0;
    const before = String(target.value || "").slice(0, pos);
    const lines = before.split("\n");
    setCursor({ line: lines.length, column: lines[lines.length - 1].length + 1 });
  }

  async function openFile(path) {
    if (!path || path === active) return;
    if (dirty) await saveFile(true);
    clearTimeout(autosaveTimer.current);
    setActive(path);
    setDirty(false);
    setStatus("Ready");
  }

  async function saveFile(silent = false) {
    const currentWs = wsRef.current;
    const currentActive = activeRef.current;
    const currentFiles = filesRef.current;
    if (!currentWs || !currentActive || saving || !dirty) return;
    setSaving(true);
    try {
      const res = await apiFetch(`/ide/workspaces/${currentWs.id}/files/`, { method: "POST", body: JSON.stringify({ path: currentActive, content: currentFiles[currentActive] || "", revision: currentWs.revision }) });
      const data = await res.json();
      setFiles(data.files || currentFiles);
      setWs((old) => old ? { ...old, files: data.files || old.files, active_file: data.active_file || currentActive, revision: data.revision ?? old.revision, updated_at: new Date().toISOString() } : old);
      setDirty(false);
      setStatus(silent ? "Saved automatically" : "Saved");
    } catch (e) { setStatus(`Save failed: ${e.message}`); } finally { setSaving(false); }
  }

  async function newFile() {
    if (!ws) return;
    const path = window.prompt("File path (example: src/App.jsx)", "src/App.jsx");
    if (!path?.trim()) return;
    if (Object.prototype.hasOwnProperty.call(files, path.trim())) return setStatus("File already exists");
    try {
      const res = await apiFetch(`/ide/workspaces/${ws.id}/files/`, { method: "POST", body: JSON.stringify({ path: path.trim(), content: "", revision: ws.revision }) });
      const data = await res.json();
      setFiles(data.files || { ...files, [path.trim()]: "" });
      setWs((old) => old ? { ...old, revision: data.revision ?? old.revision, files: data.files || old.files } : old);
      setActive(data.active_file || path.trim());
      setDirty(false);
      setStatus("New file created");
    } catch (e) { setStatus(`Create failed: ${e.message}`); }
  }

  async function renameFile() {
    if (!ws || !active) return;
    const target = window.prompt("Rename / move file", active);
    if (!target?.trim() || target.trim() === active) return;
    try {
      const res = await apiFetch(`/ide/workspaces/${ws.id}/files/`, { method: "POST", body: JSON.stringify({ action: "rename", path: active, to: target.trim(), revision: ws.revision }) });
      const data = await res.json();
      setFiles(data.files || files); setWs((old) => old ? { ...old, revision: data.revision ?? old.revision, files: data.files || old.files } : old); setActive(data.active_file || target.trim()); setDirty(false); setStatus("File moved");
    } catch (e) { setStatus(`Move failed: ${e.message}`); }
  }

  async function deleteFile() {
    if (!ws || !active || !window.confirm(`Delete ${active}?`)) return;
    try {
      const res = await apiFetch(`/ide/workspaces/${ws.id}/files/`, { method: "DELETE", body: JSON.stringify({ path: active, revision: ws.revision }) });
      const data = await res.json();
      setFiles(data.files || {}); setWs((old) => old ? { ...old, revision: data.revision ?? old.revision, files: data.files || {} } : old); setActive(data.active_file || Object.keys(data.files || {})[0] || ""); setDirty(false); setStatus("Deleted");
    } catch (e) { setStatus(`Delete failed: ${e.message}`); }
  }

  async function runCommand(e, explicitCommand = null) {
    e?.preventDefault();
    const nextCommand = String(explicitCommand ?? command).trim();
    if (!ws || !nextCommand) return;
    if (dirty) await saveFile(true);
    setStatus("Running in isolated sandbox…");
    setTerminal(`$ ${nextCommand}\n`);
    try {
      const res = await apiFetch(`/ide/workspaces/${ws.id}/execute/`, { method: "POST", body: JSON.stringify({ command: nextCommand }) });
      const data = await res.json();
      setTerminal(`$ ${nextCommand}\n${data.stdout || ""}${data.stderr ? `\n${data.stderr}` : ""}\n\n[exit ${data.exit_code}] · ${data.duration_ms || 0}ms`);
      if (data.files) setFiles(data.files);
      setCommandHistory((items) => [nextCommand, ...items.filter((x) => x !== nextCommand)].slice(0, 30));
      setHistoryIndex(-1); setCommand("");
      setStatus(data.status === "success" ? "Command completed" : "Command failed");
    } catch (e2) { setStatus(`Execution failed: ${e2.message}`); setTerminal(`$ ${nextCommand}\n${e2.message}`); }
  }

  async function install() {
    if (!ws) return;
    if (dirty) await saveFile(true);
    setStatus("Installing framework…"); setTerminal("");
    try {
      const res = await apiFetch(`/ide/workspaces/${ws.id}/install/`, { method: "POST", body: JSON.stringify({ framework }) });
      const data = await res.json();
      setTerminal(data.installation?.output || data.error || "No output");
      if (data.workspace) { setWs(data.workspace); setFiles(data.workspace.files || files); }
      setStatus(data.installation?.status === "success" ? "Framework installed" : "Install failed");
    } catch (e) { setStatus(`Install failed: ${e.message}`); }
  }

  async function installPackages() {
    if (!ws || !packages.trim()) return;
    if (dirty) await saveFile(true);
    setStatus("Installing packages…"); setTerminal("");
    try {
      const manager = ws.package_manager || frameworks.find((f) => f.id === framework)?.package_manager || "npm";
      const res = await apiFetch(`/ide/workspaces/${ws.id}/packages/`, { method: "POST", body: JSON.stringify({ package_manager: manager, packages: packages.split(",").map((x) => x.trim()).filter(Boolean) }) });
      const data = await res.json();
      setTerminal(`${data.stdout || ""}${data.stderr ? `\n${data.stderr}` : ""}`);
      if (data.workspace) { setWs(data.workspace); setFiles(data.workspace.files || files); }
      setPackages(""); setStatus(data.status === "success" ? "Packages installed" : "Package install failed");
    } catch (e) { setStatus(`Package install failed: ${e.message}`); }
  }

  const checkCommand = useMemo(() => {
    if (ws?.runtime === "python") return "python -m compileall .";
    if (ws?.runtime === "node") return "npm run build --if-present";
    return "echo 'No automatic checker configured for this runtime'";
  }, [ws?.runtime]);

  const visibleFiles = useMemo(() => Object.keys(files).filter((path) => !quickOpen || path.toLowerCase().includes(quickOpen.toLowerCase())), [files, quickOpen]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.ctrlKey || event.metaKey) {
        const key = event.key.toLowerCase();
        if (key === "s") { event.preventDefault(); void saveFile(); }
        if (key === "p") { event.preventDefault(); setQuickOpen((v) => v ? "" : " "); }
        if (key === "enter" && !event.shiftKey) { event.preventDefault(); void runCommand(); }
      }
      if (event.altKey && event.key === "ArrowUp" && commandHistory.length) {
        event.preventDefault(); const next = Math.min(historyIndex + 1, commandHistory.length - 1); setHistoryIndex(next); setCommand(commandHistory[next]);
      }
      if (event.altKey && event.key === "ArrowDown" && commandHistory.length) {
        event.preventDefault(); const next = historyIndex - 1; setHistoryIndex(next); setCommand(next >= 0 ? commandHistory[next] : "");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [commandHistory, historyIndex, command, dirty, active, ws, files]);

  return <div className="dos-ide">
    <header className="ide-header">
      <div className="ide-brand"><span className="ide-brand-mark">⌘</span><div><strong>Developer OS IDE · Pro Editor</strong><small>{ws?.name || "No workspace"} · {ws?.framework || "polyglot workspace"}</small></div></div>
      <div className="ide-actions">
        <select value={framework} onChange={(e) => setFramework(e.target.value)} aria-label="Framework">{frameworks.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}</select>
        <button onClick={install} disabled={!ws}>Install framework</button>
        <button onClick={newFile} disabled={!ws}>＋ File</button>
        <button onClick={renameFile} disabled={!active}>Rename</button>
        <button onClick={() => void saveFile()} disabled={saving || !dirty}>{saving ? "Saving…" : dirty ? "Save *" : "Saved"}</button>
        <button className="primary" onClick={() => setShowTerminal((x) => !x)}>Terminal</button>
      </div>
    </header>

    <div className="ide-packagebar">
      <span>PACKAGE MANAGER</span><b>{ws?.package_manager || frameworks.find((f) => f.id === framework)?.package_manager || "npm"}</b>
      <input value={packages} onChange={(e) => setPackages(e.target.value)} placeholder="axios, zod, django-filter…" onKeyDown={(e) => { if (e.key === "Enter") void installPackages(); }} />
      <button onClick={installPackages} disabled={!packages.trim()}>Install packages</button>
      <button className="ide-check" onClick={() => void runCommand(null, checkCommand)} disabled={!ws}>✓ Check</button>
    </div>

    <div className="ide-workspacebar">
      <select value={ws?.id || ""} onChange={(e) => void openWorkspace(workspaces.find((x) => String(x.id) === e.target.value))} aria-label="Workspace">
        {workspaces.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
      </select>
      <button onClick={createWorkspace}>New workspace</button>
      <button onClick={() => setQuickOpen(" ")}>Quick Open ⌘P</button>
      <span className={`ide-status ${/failed|error/i.test(status) ? "bad" : ""}`}>{status}</span>
    </div>

    {quickOpen !== "" && <div className="ide-quick-open"><input autoFocus value={quickOpen.trim()} onChange={(e) => setQuickOpen(e.target.value)} placeholder="Type a filename…" onKeyDown={(e) => { if (e.key === "Escape") setQuickOpen(""); }} />{visibleFiles.slice(0, 12).map((path) => <button key={path} onClick={() => { void openFile(path); setQuickOpen(""); }}>{path}<small>{extLanguage(path)}</small></button>)}</div>}

    <div className="ide-main">
      <Tree files={files} active={active} onOpen={(path) => { void openFile(path); }} />
      <section className="ide-center">
        <div className="ide-tabs">
          {active && <div className="ide-tab active"><span>{active.split("/").pop()}</span>{dirty && <b>●</b>}<em>{extLanguage(active)}</em><button onClick={deleteFile} title="Delete file">×</button></div>}
        </div>
        <div className="ide-editor">
          <MonacoEditor path={active} value={files[active] || ""} onChange={updateContent} onCursorChange={setCursor} />
        </div>
        <footer className="ide-footer"><span>{active || "No file selected"}</span><span>Ln {cursor.line}, Col {cursor.column}</span><span>{extLanguage(active)} · UTF-8</span><span>{(files[active] || "").length.toLocaleString()} chars</span><span>{dirty ? "Modified" : "Synced"}</span></footer>
      </section>
      {showTerminal && <aside className="ide-terminal">
        <div className="terminal-head"><strong>TERMINAL</strong><span>Isolated · 120s max · network off</span><button onClick={() => setTerminal("")}>Clear</button></div>
        <pre>{terminal || "Developer OS secure runner\n\nCtrl+Enter  Run command\nCtrl+S      Save\nAlt+↑/↓     Command history\nCtrl+P      Quick Open\n\nExamples:\n  npm run build\n  python manage.py check\n  python -m pytest"}</pre>
        <form onSubmit={runCommand}><span>›</span><input value={command} onChange={(e) => { setCommand(e.target.value); setHistoryIndex(-1); }} placeholder="Run a command…" autoComplete="off" /></form>
      </aside>}
    </div>
  </div>;
}
