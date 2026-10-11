import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../services/api";
import "./ProjectsExperience.css";

const STATUSES = ["Planning", "In Progress", "On Hold", "Completed"];
const CATEGORIES = ["General", "Frontend", "Backend", "DevOps", "AI", "Design", "Productivity"];
const PRIORITIES = ["low", "medium", "high", "urgent"];

function normalizeList(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.results)) return payload.results;
  return [];
}

async function readResponse(response, fallback) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload?.detail || payload?.error || payload?.message;
    const fieldError = Object.values(payload || {}).find((value) => Array.isArray(value) && value.length)?.[0];
    throw new Error(typeof detail === "string" ? detail : fieldError || fallback);
  }
  return payload;
}

function ProjectCard({ project, onOpen, onTogglePublic, togglingPublic, canManageVisibility }) {
  const progress = Math.max(0, Math.min(100, Number(project.progress) || 0));
  const title = project.title || "Untitled project";
  const status = project.status || "Planning";
  const priority = String(project.priority || "medium").toLowerCase();

  return (
    <article className="px-project-card">
      <button className="px-card-open" type="button" onClick={() => onOpen(project)} aria-label={`Open project: ${title}`}>
        <div className="px-card-topline">
          <span className="px-category"><i aria-hidden="true" />{project.category || "General"}</span>
          <span className={`px-priority px-priority-${priority}`}>{priority}</span>
        </div>
        <h3>{title}</h3>
        <p>{project.description || "Add a short description to make this project easier to understand."}</p>
        <div className="px-progress-label"><span>Delivery progress</span><strong>{progress}%</strong></div>
        <div className="px-progress-track" role="progressbar" aria-label={`${title} progress`} aria-valuemin="0" aria-valuemax="100" aria-valuenow={progress}><span style={{ width: `${progress}%` }} /></div>
        <div className="px-card-footer"><span className={`px-status px-status-${status.toLowerCase().replace(/\s+/g, "-")}`}>{status}</span><span>{project.deadline ? `Due ${project.deadline}` : "No deadline"}</span></div>
      </button>
      <div className="px-card-actions"><button className="px-open-link" type="button" onClick={() => onOpen(project)}>Open workspace <span aria-hidden="true">↗</span></button>{canManageVisibility && <button className={`px-public-toggle ${project.is_public ? "is-public" : ""}`} type="button" onClick={() => onTogglePublic(project)} disabled={togglingPublic} title={project.is_public ? "Remove this project from the public gallery" : "Publish this project to the public gallery"}>{togglingPublic ? "Saving…" : project.is_public ? "◉ Public" : "○ Make public"}</button>}</div>
    </article>
  );
}

export default function ProjectsExperience({ go, currentUserId }) {
  const [items, setItems] = useState([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [category, setCategory] = useState("all");
  const [priority, setPriority] = useState("all");
  const [sort, setSort] = useState("updated");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [togglingPublicId, setTogglingPublicId] = useState(null);
  const [formError, setFormError] = useState("");
  const [form, setForm] = useState({ title: "", description: "", category: "General", status: "Planning", priority: "medium", deadline: "", is_public: false });

  const load = useCallback(async ({ quiet = false } = {}) => {
    if (quiet) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/projects/");
      const payload = await readResponse(response, "Could not load projects.");
      setItems(normalizeList(payload));
    } catch (err) {
      setError(err?.message || "Could not load projects. Check your connection and try again.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return items.filter((project) => {
      const searchable = [project.title, project.description, project.category, project.status, ...(Array.isArray(project.tags) ? project.tags : []), ...(Array.isArray(project.stack) ? project.stack : [])].join(" ").toLowerCase();
      return (!needle || searchable.includes(needle))
        && (status === "all" || String(project.status || "").toLowerCase() === status.toLowerCase())
        && (category === "all" || String(project.category || "").toLowerCase() === category.toLowerCase())
        && (priority === "all" || String(project.priority || "medium").toLowerCase() === priority);
    }).sort((a, b) => {
      if (sort === "title") return String(a.title || "").localeCompare(String(b.title || ""));
      if (sort === "deadline") return (a.deadline || "9999-12-31").localeCompare(b.deadline || "9999-12-31");
      if (sort === "priority") return PRIORITIES.indexOf(String(b.priority || "medium").toLowerCase()) - PRIORITIES.indexOf(String(a.priority || "medium").toLowerCase());
      return String(b.uploaded_at || b.updated_at || b.created_at || "").localeCompare(String(a.uploaded_at || a.updated_at || a.created_at || ""));
    });
  }, [items, query, status, category, priority, sort]);

  const counts = useMemo(() => ({
    total: items.length,
    active: items.filter((p) => !["completed", "on hold"].includes(String(p.status || "").toLowerCase())).length,
    completed: items.filter((p) => String(p.status || "").toLowerCase() === "completed").length,
    urgent: items.filter((p) => String(p.priority || "").toLowerCase() === "urgent").length,
  }), [items]);

  const createProject = async (event) => {
    event.preventDefault();
    if (!form.title.trim()) {
      setFormError("Give your project a name before creating it.");
      return;
    }
    setSaving(true);
    setFormError("");
    setError("");
    try {
      const payload = { title: form.title.trim(), description: form.description.trim(), category: form.category, status: form.status, priority: form.priority, is_public: form.is_public, tags: [], stack: [] };
      if (form.deadline) payload.deadline = form.deadline;
      const response = await apiFetch("/projects/", { method: "POST", body: JSON.stringify(payload) });
      const created = await readResponse(response, "Project could not be created.");
      setForm({ title: "", description: "", category: "General", status: "Planning", priority: "medium", deadline: "", is_public: false });
      setFormOpen(false);
      setNotice("Project created. Your workspace is ready.");
      if (created && created.id) setItems((previous) => [created, ...previous.filter((item) => item.id !== created.id)]);
      else await load({ quiet: true });
    } catch (err) {
      setFormError(err?.message || "Project creation failed. Please try again.");
    } finally {
      setSaving(false);
    }
  };

  const togglePublic = async (project) => {
    setTogglingPublicId(project.id);
    setError("");
    try {
      const response = await apiFetch(`/projects/${project.id}/`, { method: "PATCH", body: JSON.stringify({ is_public: !project.is_public }) });
      const updated = await readResponse(response, "Project visibility could not be updated.");
      setItems((previous) => previous.map((item) => item.id === project.id ? { ...item, ...updated } : item));
      setNotice(updated.is_public ? "Project published to the public gallery." : "Project removed from the public gallery.");
    } catch (err) {
      setError(err?.message || "Project visibility could not be updated.");
    } finally {
      setTogglingPublicId(null);
    }
  };

  const clearFilters = () => { setQuery(""); setStatus("all"); setCategory("all"); setPriority("all"); setSort("updated"); };

  return (
    <main className="px-projects-page">
      <header className="px-projects-hero">
        <div className="px-hero-copy">
          <div className="px-eyebrow"><span /> ENGINEERING WORKSPACE <b> / PROJECTS</b></div>
          <h1>Build with <span>intent.</span></h1>
          <p>One operational view for every idea, delivery milestone, and engineering initiative.</p>
          <div className="px-hero-actions">
            <button className="px-primary" type="button" onClick={() => { setFormOpen(true); setFormError(""); }}>＋ New project</button>
            <button className="px-secondary" type="button" onClick={() => go("/ide")}>Open Web IDE <span aria-hidden="true">⌘</span></button>
          </div>
        </div>
        <div className="px-hero-orbit" aria-hidden="true"><div className="px-orbit-ring px-ring-one" /><div className="px-orbit-ring px-ring-two" /><div className="px-orbit-core"><span>DEV</span><b>OS</b></div><i className="px-orbit-node px-node-one" /><i className="px-orbit-node px-node-two" /><i className="px-orbit-node px-node-three" /></div>
      </header>

      <section className="px-project-stats" aria-label="Project summary">
        <article><span className="px-stat-icon">◈</span><div><small>Total projects</small><strong>{loading ? "—" : counts.total}</strong></div><em>All workspaces</em></article>
        <article><span className="px-stat-icon px-icon-cyan">↗</span><div><small>In motion</small><strong>{loading ? "—" : counts.active}</strong></div><em>Active initiatives</em></article>
        <article><span className="px-stat-icon px-icon-green">✓</span><div><small>Completed</small><strong>{loading ? "—" : counts.completed}</strong></div><em>Shipped projects</em></article>
        <article><span className="px-stat-icon px-icon-amber">!</span><div><small>Urgent priority</small><strong>{loading ? "—" : counts.urgent}</strong></div><em>Needs attention</em></article>
      </section>

      <section className="px-project-controls" aria-label="Project controls">
        <div className="px-control-heading"><div><span className="px-section-kicker">YOUR PORTFOLIO</span><h2>Project spaces <span>{filtered.length}</span></h2></div><button type="button" className="px-refresh" onClick={() => load({ quiet: true })} disabled={loading || refreshing}>{refreshing ? "Refreshing…" : "↻ Refresh"}</button></div>
        <div className="px-filter-row">
          <label className="px-project-search"><span aria-hidden="true">⌕</span><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search projects, tags, tech stack…" aria-label="Search projects" /><kbd>⌘ K</kbd></label>
          <label className="px-select-wrap"><span>Status</span><select value={status} onChange={(e) => setStatus(e.target.value)}><option value="all">All statuses</option>{STATUSES.map((v) => <option key={v} value={v}>{v}</option>)}</select></label>
          <label className="px-select-wrap"><span>Category</span><select value={category} onChange={(e) => setCategory(e.target.value)}><option value="all">All categories</option>{CATEGORIES.map((v) => <option key={v} value={v}>{v}</option>)}</select></label>
          <label className="px-select-wrap"><span>Priority</span><select value={priority} onChange={(e) => setPriority(e.target.value)}><option value="all">All priorities</option>{PRIORITIES.map((v) => <option key={v} value={v}>{v[0].toUpperCase() + v.slice(1)}</option>)}</select></label>
          <label className="px-select-wrap px-sort-wrap"><span>Sort by</span><select value={sort} onChange={(e) => setSort(e.target.value)}><option value="updated">Recently updated</option><option value="title">Name A–Z</option><option value="deadline">Nearest deadline</option><option value="priority">Priority</option></select></label>
        </div>
      </section>

      {error && <div className="px-message px-error" role="alert"><span>!</span><div><strong>Workspace could not sync</strong><p>{error}</p></div><button type="button" onClick={() => load()}>Try again</button></div>}
      {notice && <div className="px-message px-success" role="status"><span>✓</span><p>{notice}</p><button type="button" onClick={() => setNotice("")} aria-label="Dismiss message">×</button></div>}

      {loading ? <div className="px-project-grid" aria-label="Loading projects">{[1, 2, 3].map((n) => <div className="px-skeleton" key={n}><i /><b /><span /><span /><em /></div>)}</div>
        : filtered.length ? <div className="px-project-grid">{filtered.map((project) => <ProjectCard key={project.id ?? project.title} project={project} onOpen={(p) => go(`/projects/${p.id}`)} onTogglePublic={togglePublic} togglingPublic={togglingPublicId === project.id} canManageVisibility={currentUserId != null && String(project.owner_id) === String(currentUserId)} />)}</div>
        : <section className="px-empty-state"><div className="px-empty-glyph">◈</div><h3>{items.length ? "No matching projects" : "Your next big thing starts here."}</h3><p>{items.length ? "Try changing the search or filters to find a project." : "Create a project to bring its goals, tasks, and delivery signals into one focused workspace."}</p>{items.length ? <button type="button" className="px-secondary" onClick={clearFilters}>Clear filters</button> : <button type="button" className="px-primary" onClick={() => setFormOpen(true)}>＋ Create your first project</button>}</section>}

      {formOpen && <div className="px-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setFormOpen(false); }}>
        <section className="px-create-modal" role="dialog" aria-modal="true" aria-labelledby="px-create-title">
          <div className="px-modal-head"><div><span className="px-section-kicker">NEW WORKSPACE</span><h2 id="px-create-title">Create a project</h2><p>Set the foundation. You can refine the details later.</p></div><button type="button" onClick={() => !saving && setFormOpen(false)} aria-label="Close project form" disabled={saving}>×</button></div>
          <form onSubmit={createProject}>
            <label className="px-form-field px-field-full"><span>Project name <b>*</b></span><input autoFocus maxLength={255} required value={form.title} onChange={(e) => setForm((p) => ({ ...p, title: e.target.value }))} placeholder="e.g. Developer OS 5.0" /></label>
            <label className="px-form-field px-field-full"><span>Description</span><textarea rows={3} value={form.description} onChange={(e) => setForm((p) => ({ ...p, description: e.target.value }))} placeholder="What are you building and why?" /></label>
            <label className="px-public-field"><input type="checkbox" checked={form.is_public} onChange={(e) => setForm((p) => ({ ...p, is_public: e.target.checked }))} /><span><strong>Publish to public gallery</strong><small>Anyone using Developer OS can discover this project. Only share links and details intended to be public.</small></span></label>
            <div className="px-form-grid">
              <label className="px-form-field"><span>Category</span><select value={form.category} onChange={(e) => setForm((p) => ({ ...p, category: e.target.value }))}>{CATEGORIES.map((v) => <option key={v}>{v}</option>)}</select></label>
              <label className="px-form-field"><span>Initial status</span><select value={form.status} onChange={(e) => setForm((p) => ({ ...p, status: e.target.value }))}>{STATUSES.map((v) => <option key={v}>{v}</option>)}</select></label>
              <label className="px-form-field"><span>Priority</span><select value={form.priority} onChange={(e) => setForm((p) => ({ ...p, priority: e.target.value }))}>{PRIORITIES.map((v) => <option key={v} value={v}>{v[0].toUpperCase() + v.slice(1)}</option>)}</select></label>
              <label className="px-form-field"><span>Target deadline</span><input type="date" value={form.deadline} onChange={(e) => setForm((p) => ({ ...p, deadline: e.target.value }))} /></label>
            </div>
            {formError && <div className="px-form-error" role="alert">{formError}</div>}
            <div className="px-modal-actions"><button className="px-secondary" type="button" disabled={saving} onClick={() => setFormOpen(false)}>Cancel</button><button className="px-primary" type="submit" disabled={saving}>{saving ? "Creating…" : "Create project →"}</button></div>
          </form>
        </section>
      </div>}
    </main>
  );
}
