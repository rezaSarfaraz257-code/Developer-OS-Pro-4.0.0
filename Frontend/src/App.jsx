import { useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import "./ExploreModern.css";
import "./NavigationPolish.css";
import FullscreenExperience from "./components/FullscreenExperience.jsx";
import { API_URL, apiFetch, clearAuth, getAccessToken, setAuthTokens, revokeRefreshToken, formatApiError } from "./services/api";
import ProIDE from "./ProIDE";
import Avatar from "./components/Avatar";
import { normalizeProfileLink, safeProfileLink } from "./utils/profile.js";
import Referral from "./Referral";

const nav = [
  ["dashboard", "⌂", "Command Center"],
  ["explore", "◌", "Explore"],
  ["projects", "◈", "Projects"],
  ["ide", "⌘", "Web IDE"],
  ["ai", "✦", "Intelligence"],
  ["search", "⌕", "Universal Search"],
  ["team", "◎", "Team & Collab"],
  ["referrals", "↗", "Invite & Earn"],
  ["billing", "◇", "SaaS / Billing"],
  ["profile", "◎", "My Profile"],
  ["settings", "⚙", "Settings"],
  ["audit", "≡", "Audit Log"],
];

function useRoute() {
  const [path, setPath] = useState(window.location.pathname || "/");
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname || "/");
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return [path, (next) => {
    window.history.pushState({}, "", next);
    setPath(next);
  }];
}


async function register(payload) {
  const r = await fetch(`${API_URL}/register/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(formatApiError(data, "Registration failed."));
}

function Shell({ user, onLogout, children, go, current }) {
  const [search, setSearch] = useState("");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const searchRef = useRef(null);
  const [notifications, setNotifications] = useState({ unread: 0, items: [] });
  const [openNotif, setOpenNotif] = useState(false);
  const [plan, setPlan] = useState("free");

  useEffect(() => {
    apiFetch("/usage/").then(r => r.json()).then(d => setPlan(d.plan || "free")).catch(() => {});
  }, []);

  useEffect(() => {
    let active = true;
    apiFetch("/notifications/").then(r => r.json()).then(d => active && setNotifications(d)).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const onKeyDown = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault(); setPaletteOpen(true);
        requestAnimationFrame(() => searchRef.current?.focus());
      }
      if (event.key === "Escape") { setPaletteOpen(false); setOpenNotif(false); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
  const commands = useMemo(() => [
    ["Command Center", "/", "⌂"], ["Web IDE", "/ide", "⌘"], ["Projects", "/projects", "◈"],
    ["Intelligence", "/ai", "✦"], ["Universal Search", "/search", "⌕"], ["Team & Collaboration", "/team", "◎"],
    ["Invite & Earn", "/referrals", "↗"], ["Billing & Plans", "/billing", "◇"], ["My Profile", "/profile", "◎"], ["Settings", "/settings", "⚙"],
  ], []);

  const runSearch = (e) => {
    if (e.key === "Enter" && search.trim()) {
      go(`/search?q=${encodeURIComponent(search.trim())}`);
      setSearch("");
    }
  };

  return (
    <div className="os-shell">
      <aside className="os-rail">
        <button
          className="brand rail-identity"
          onClick={() => go("/profile")}
          title={`${user.full_name || [user.first_name, user.last_name].filter(Boolean).join(" ") || user.username || "Developer"} · @${user.username || "developer"}`}
          aria-label="Open my profile"
        >
          <Avatar
            imageUrl={user.avatar_url}
            initials={(user.full_name || [user.first_name, user.last_name].filter(Boolean).join(" ") || user.username || "D").trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase()}
            className="rail-profile-avatar"
            label={user.full_name || user.username || "Profile"}
          />
          <span className="brand-text">
            <strong>{user.full_name || [user.first_name, user.last_name].filter(Boolean).join(" ") || user.username || "Developer"}</strong>
            <span>@{user.username || "developer"}</span>
          </span>
        </button>
        <div className="rail-section">WORKSPACE</div>
        {nav.slice(0, 6).map(([id, icon, label]) => (
          <button key={id} title={label} aria-label={label} className={`rail-item ${current === id ? "active" : ""}`} onClick={() => go(id === "dashboard" ? "/" : `/${id}`)}>
            <span>{icon}</span><em>{label}</em>
          </button>
        ))}
        <div className="rail-section">SYSTEM</div>
        {nav.slice(6).map(([id, icon, label]) => {
          const teamLocked = id === "team" && !["team","enterprise"].includes(plan);
          const auditLocked = id === "audit" && !["team","enterprise"].includes(plan);
          const locked = teamLocked || auditLocked;
          return <button key={id} title={locked ? `${label} — Team or Enterprise` : label} aria-label={label} className={`rail-item ${current === id ? "active" : ""} ${locked ? "locked" : ""}`} onClick={() => go(locked ? "/billing" : `/${id}`)}>
            <span>{icon}</span><em>{label}{locked ? " · PRO" : ""}</em>
          </button>;
        })}
        <div className="rail-spacer" />
        <div className="status-chip"><i /> SYSTEM ONLINE</div>
      </aside>

      <main className="os-main">
        <header className="topbar">
          <div className="crumb"><span>DEVELOPER OS</span><b>/</b><strong>{current === "dashboard" ? "COMMAND CENTER" : current.toUpperCase()}</strong></div>
          <div className="top-actions">
            <div className={`global-search ${paletteOpen ? "is-command-active" : ""}`}>
              
              <span>⌕</span><input ref={searchRef} value={search} onChange={e => setSearch(e.target.value)} onKeyDown={runSearch} placeholder="Search everything  /  Ctrl K" />
            </div>
            <button className="icon-btn" onClick={() => setOpenNotif(v => !v)}>◌<sup>{notifications.unread || ""}</sup></button>
            <button className="icon-btn profile-shortcut" onClick={() => go("/profile")} title="My profile" aria-label="My profile"><Avatar imageUrl={user.avatar_url} initials={(user.full_name || [user.first_name, user.last_name].filter(Boolean).join(" ") || user.username || "D").trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase()} label="My profile" /></button>
            <button className="icon-btn" onClick={() => go("/ide")}>⌘</button>
            <button className="logout-btn" onClick={() => { revokeRefreshToken(); clearAuth(); onLogout(); }}>EXIT</button>
          </div>
          {paletteOpen && <div className="command-palette" role="dialog" aria-label="Command palette">
            <div className="command-palette-head"><span>COMMAND CENTER</span><kbd>ESC</kbd></div>
            <div className="command-list">
              {commands.map(([label, path, icon]) => <button key={path} onClick={() => { setPaletteOpen(false); go(path); }}><span>{icon}</span><b>{label}</b><small>{path}</small></button>)}
            </div>
          </div>}
          {openNotif && <div className="notif-pop">
            <div className="pop-head"><b>Notifications</b><button onClick={() => apiFetch("/notifications/", {method:"PATCH",body:JSON.stringify({})}).then(() => setNotifications(n => ({...n, unread:0})))}>Mark read</button></div>
            {(notifications.items || []).slice(0, 8).map(n => <div className="notif" key={n.id}><b>{n.title}</b><span>{n.body}</span></div>)}
            {!notifications.items?.length && <div className="empty">No notifications.</div>}
          </div>}
        </header>
        <div className="os-content">{children}</div>
      </main>
    </div>
  );
}

function Auth({ onReady }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ username:"", password:"", email:"", first_name:"", last_name:"", otp:"", backup_code:"" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [path] = useState(window.location.pathname);
  const [linkState, setLinkState] = useState(path === "/verify-email" ? "verifying" : "idle");
  const [resetPassword, setResetPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [confirmPassword, setConfirmPassword] = useState("");
  const passwordStrength = [form.password.length >= 12, /[A-Z]/.test(form.password), /[a-z]/.test(form.password), /\d/.test(form.password), /[^A-Za-z0-9]/.test(form.password)].filter(Boolean).length;

  useEffect(() => {
    if (path !== "/verify-email") return;
    const params = new URLSearchParams(window.location.search);
    const uid = params.get("uid");
    const token = params.get("token");
    // Keep one-time verification tokens out of the visible URL and browser history.
    window.history.replaceState({}, document.title, window.location.pathname);
    fetch(`${API_URL}/auth/verify-email/`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({uid, token})})
      .then(async r => { const d=await r.json().catch(()=>({})); if(!r.ok) throw new Error(formatApiError(d, "Verification failed.")); setNotice("Email verified successfully. You can sign in now."); setLinkState("verified"); })
      .catch(e => { setError(e.message); setLinkState("error"); });
  }, [path]);

  const submit = async (e) => {
  e.preventDefault();
  setBusy(true);
  setError("");
  setNotice("");

  try {
    if (mode === "register") {
      if (form.password !== confirmPassword) throw new Error("Passwords do not match.");
      if (form.password.length < 12) throw new Error("Use at least 12 characters for your password.");
      await register({
        username: form.username,
        password: form.password,
        email: form.email,
        first_name: form.first_name,
        last_name: form.last_name,
      });

      setMode("verify");
      setForm((previous) => ({...previous, password: ""}));
      setConfirmPassword("");
      setNotice(
        "Account created. A verification email has been queued. Check your inbox and spam folder; if it does not arrive, request another link."
      );
    } else if (mode === "forgot") {
      const response = await fetch(`${API_URL}/auth/password-reset/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email: form.email,
        }),
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          formatApiError(data, "Unable to request password reset.")
        );
      }

      setNotice(
        data.detail || "If the account exists, a reset email is on the way."
      );
    } else if (mode === "verify") {
      const response = await fetch(
        `${API_URL}/auth/resend-verification/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            email: form.email,
          }),
        }
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          formatApiError(data, "Unable to resend verification.")
        );
      }

      setNotice(data.detail || "Verification email queued.");
    } else if (mode === "reset") {
      const params = new URLSearchParams(window.location.search);

      const response = await fetch(
        `${API_URL}/auth/password-reset/confirm/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            uid: params.get("uid"),
            token: params.get("token"),
            password: resetPassword,
          }),
        }
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          formatApiError(data, "Password reset failed.")
        );
      }

      setMode("login");
      setNotice("Password changed. Sign in with your new password.");
    } else {
      const response = await fetch(`${API_URL}/token/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          username: form.username,
          password: form.password,
          otp: form.otp,
          backup_code: form.backup_code,
        }),
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          formatApiError(data, "Invalid credentials.")
        );
      }

      if (!setAuthTokens(data.access, data.refresh)) {
        throw new Error("Authentication succeeded but no valid session tokens were returned.");
      }

      onReady();
    }
  } catch (err) {
    setError(
      typeof err.message === "string"
        ? err.message
        : "Authentication failed."
    );
  } finally {
    setBusy(false);
  }
};
  if (path === "/reset-password") return <div className="auth-screen"><div className="auth-grid"/><div className="auth-card"><div className="auth-logo"><span>D</span><div>DEVELOPER OS<small>ACCOUNT RECOVERY</small></div></div><div className="auth-copy"><span>SECURE RESET</span><h1>Set a new password.</h1><p>Use a strong password of at least 12 characters. The reset token is single-use and time-limited.</p></div><form onSubmit={async (e)=>{e.preventDefault();setBusy(true);setError("");try{const params=new URLSearchParams(window.location.search);const r=await fetch(`${API_URL}/auth/password-reset/confirm/`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({uid:params.get("uid"),token:params.get("token"),password:resetPassword})});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(formatApiError(d, "Password reset failed."));window.location.assign("/")}catch(err){setError(err.message)}finally{setBusy(false)}}}><input required type="password" minLength="12" placeholder="New password" value={resetPassword} onChange={e=>setResetPassword(e.target.value)}/>{error&&<div className="error">{error}</div>}<button className="primary wide" disabled={busy}>{busy?"UPDATING...":"RESET PASSWORD →"}</button></form></div></div>;

  if (path === "/verify-email") return <div className="auth-screen"><div className="auth-grid"/><div className="auth-card"><div className="auth-logo"><span>{linkState === "verified" ? "✓" : "D"}</span><div>DEVELOPER OS<small>EMAIL SECURITY</small></div></div><div className="auth-copy"><span>VERIFICATION</span><h1>{linkState === "verifying" ? "Verifying your email…" : linkState === "verified" ? "Email verified." : "Verification failed."}</h1><p>{notice || error || "Checking the secure verification link."}</p></div><button className="primary wide" onClick={()=>window.location.assign("/")}>CONTINUE TO SIGN IN →</button></div></div>;

  if (mode === "verify") return <div className="auth-screen"><div className="auth-grid"/><div className="auth-card"><div className="auth-logo"><span>✓</span><div>VERIFY DEVELOPER OS<small>SECURE ACCOUNT ACTIVATION</small></div></div><div className="auth-copy"><span>EMAIL VERIFICATION</span><h1>One last step.</h1><p>Verify the email address on your Developer OS account. You can request another link below.</p></div><form onSubmit={submit}><input required type="email" placeholder="name@example.com" value={form.email} onChange={e=>setForm({...form,email:e.target.value})}/>{notice&&<div className="notice">{notice}</div>}{error&&<div className="error">{error}</div>}<button className="primary wide" disabled={busy}>{busy?"QUEUING...":"RESEND VERIFICATION →"}</button></form><button className="switch" onClick={()=>{setMode("login");setError("")}}>Back to sign in</button></div></div>;

  return <div className="auth-screen"><div className="auth-grid"/><div className="auth-card">
    <div className="auth-logo"><span>D</span><div>DEVELOPER OS<small>THE OPERATING SYSTEM FOR DEVELOPERS</small></div></div>
    <div className="auth-copy"><span>{mode === "forgot" ? "ACCOUNT RECOVERY" : "BOOT SEQUENCE"}</span><h1>{mode === "login" ? "Enter the command center." : mode === "register" ? "Initialize your workspace." : "Recover your account."}</h1><p>{mode === "forgot" ? "We will send a secure, time-limited password reset link." : "Projects, intelligence, code, collaboration and delivery in one developer control plane."}</p></div>
    <form onSubmit={submit} className="auth-form">
      {mode === "register" && <div className="two">
        <label className="auth-field"><span>FIRST NAME</span><input autoComplete="given-name" placeholder="First name" value={form.first_name} onChange={e=>setForm({...form,first_name:e.target.value})}/></label>
        <label className="auth-field"><span>LAST NAME</span><input autoComplete="family-name" placeholder="Last name" value={form.last_name} onChange={e=>setForm({...form,last_name:e.target.value})}/></label>
      </div>}
      {mode !== "forgot" && <label className="auth-field"><span>{mode === "register" ? "USERNAME" : "USERNAME OR EMAIL"}</span><input required minLength={mode === "register" ? 3 : undefined} autoComplete="username" placeholder={mode === "register" ? "Choose a username" : "Username or email"} value={form.username} onChange={e=>setForm({...form,username:e.target.value})}/></label>}
      {(mode === "register" || mode === "forgot") && <label className="auth-field"><span>EMAIL ADDRESS</span><input required type="email" autoComplete="email" placeholder="you@example.com" value={form.email} onChange={e=>setForm({...form,email:e.target.value})}/></label>}
      {mode !== "forgot" && <label className="auth-field"><span>PASSWORD</span><div className="auth-password"><input required type={showPassword ? "text" : "password"} minLength={mode === "register" ? 12 : undefined} autoComplete={mode === "register" ? "new-password" : "current-password"} placeholder={mode === "register" ? "At least 12 characters" : "Your password"} value={form.password} onChange={e=>setForm({...form,password:e.target.value})}/><button type="button" className="password-toggle" aria-label={showPassword ? "Hide password" : "Show password"} onClick={()=>setShowPassword(v=>!v)}>{showPassword ? "HIDE" : "SHOW"}</button></div></label>}
      {mode === "register" && <>
        <div className="password-meter" aria-label={`Password strength: ${passwordStrength} of 5`}><div className={`strength strength-${passwordStrength}`} /><div className={`strength strength-${passwordStrength}`} /><div className={`strength strength-${passwordStrength}`} /><div className={`strength strength-${passwordStrength}`} /><div className={`strength strength-${passwordStrength}`} /></div>
        <p className="password-hint">{form.password ? ["Very weak","Weak","Fair","Good","Strong","Excellent"][passwordStrength] : "Use 12+ characters with a mix of upper/lowercase, numbers and symbols."}</p>
        <label className="auth-field"><span>CONFIRM PASSWORD</span><input required type={showPassword ? "text" : "password"} autoComplete="new-password" placeholder="Enter your password again" value={confirmPassword} onChange={e=>setConfirmPassword(e.target.value)}/></label>
      </>}
      {mode === "login" && <div className="two">
        <label className="auth-field"><span>AUTHENTICATOR CODE (OPTIONAL)</span><input inputMode="numeric" autoComplete="one-time-code" aria-label="Authenticator code" placeholder="6-digit code" value={form.otp} onChange={e=>setForm({...form,otp:e.target.value})}/></label>
        <label className="auth-field"><span>BACKUP CODE</span><input autoComplete="off" aria-label="Backup code" placeholder="Recovery code" value={form.backup_code} onChange={e=>setForm({...form,backup_code:e.target.value})}/></label>
      </div>}
      {notice&&<div className="notice" role="status">{notice}</div>}{error&&<div className="error" role="alert">{error}</div>}
      <button className="primary wide auth-submit" disabled={busy}>{busy?"PROCESSING...":mode === "login" ? "ACCESS COMMAND CENTER →" : mode === "register" ? "CREATE DEVELOPER ID →" : "SEND RESET LINK →"}</button>
    </form>
    {mode === "login" && <button className="switch" onClick={()=>{setForm(previous=>({...previous,email:previous.email || (previous.username.includes("@") ? previous.username : "")}));setMode("forgot");setError("");setNotice("")}}>Forgot password?</button>}
    {mode === "login" && <button className="switch" onClick={()=>{setForm(previous=>({...previous,email:previous.email || (previous.username.includes("@") ? previous.username : "")}));setMode("verify");setError("");setNotice("")}}>Resend verification email</button>}
    {mode !== "forgot" && <button className="switch" onClick={()=>{setMode(mode==="login"?"register":"login");setError("");setNotice("")}}>{mode==="login" ? "Create a new Developer OS account" : "I already have an account"}</button>}
    {mode === "forgot" && <button className="switch" onClick={()=>{setMode("login");setError("");setNotice("")}}>Back to sign in</button>}
  </div></div>;
}
function Dashboard({ go }) {
  const [data, setData] = useState(null);
  const [projects, setProjects] = useState([]);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const load = () => {
    Promise.all([
      apiFetch("/workspace/summary/").then(r=>r.json()),
      apiFetch("/projects/").then(r=>r.json()),
    ]).then(([a,p]) => {setData(a); setProjects(Array.isArray(p)?p:(p.results||[]));}).catch(e=>setError(e.message));
  };
  useEffect(load, []);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await apiFetch("/projects/", {method:"POST", body:JSON.stringify({title:name.trim(),description:"",category:"General",status:"Planning",priority:"medium",tags:[],stack:[]})});
      setName(""); load();
    } catch(e) { setError(e.message); } finally {setBusy(false);}
  };

  const stats = [
    ["PROJECTS", data?.projects ?? "—", "workspace"],
    ["ACTIVE TASKS", data?.active_tasks ?? "—", `${data?.urgent_tasks || 0} urgent`],
    ["COMPLETION", `${data?.completion ?? 0}%`, `${data?.done_tasks || 0} shipped`],
    ["BLOCKED", data?.blocked_tasks ?? "—", `${data?.overdue_tasks || 0} overdue`],
  ];

  return <div className="page">
    <div className="hero-row"><div><div className="eyebrow">DEVELOPER OS / 01</div><h1>Command Center</h1><p>One control plane for building, reasoning, collaborating and shipping software.</p></div><div className="hero-actions"><button className="ghost" onClick={()=>go("/ide")}>OPEN IDE ⌘</button><button className="primary" onClick={()=>document.getElementById("new-project")?.focus()}>+ NEW PROJECT</button></div></div>
    <div className="stat-grid">{stats.map(s=><div className="stat-card" key={s[0]}><span>{s[0]}</span><strong>{s[1]}</strong><small>{s[2]}</small></div>)}</div>
    <div className="dashboard-grid">
      <section className="panel wide-panel"><div className="panel-head"><div><span className="panel-kicker">DELIVERY GRAPH</span><h2>Project trajectory</h2></div><button className="text-btn" onClick={()=>go("/projects")}>VIEW ALL →</button></div>
        <div className="project-list">{(data?.project_completion||[]).map(p=><div className="project-line" key={p.id} onClick={()=>go(`/projects/${p.id}`)}><div><b>{p.title}</b><small>{p.status} · {p.tasks} tasks</small></div><div className="bar"><i style={{width:`${p.progress}%`}} /></div><strong>{p.progress}%</strong></div>)}
        {!data?.project_completion?.length && <div className="empty">No project signals yet. Create the first project.</div>}</div>
      </section>
      <section className="panel intelligence-card"><div className="ai-orb">✦</div><span className="panel-kicker">INTELLIGENCE ENGINE</span><h2>Context-aware AI</h2><p>Ask about your projects, tasks, notes, snippets and delivery risks.</p><button className="primary wide" onClick={()=>go("/ai")}>OPEN INTELLIGENCE →</button></section>
    </div>
    <section className="panel"><div className="panel-head"><div><span className="panel-kicker">PROJECTS</span><h2>Workspace</h2></div><div className="inline-create"><input id="new-project" value={name} onChange={e=>setName(e.target.value)} onKeyDown={e=>e.key==="Enter"&&create()} placeholder="New project name..." /><button className="primary" disabled={busy} onClick={create}>CREATE</button></div></div>
      {error&&<div className="error">{error}</div>}
      <div className="cards-grid">{projects.slice(0,6).map(p=><button className="project-card" key={p.id} onClick={()=>go(`/projects/${p.id}`)}><span>{p.category||"GENERAL"}</span><b>{p.title}</b><small>{p.description||"No description yet."}</small><div className="card-meta"><i>{p.status}</i><strong>{p.progress??0}%</strong></div></button>)}</div>
    </section>
  </div>;
}

function Projects({ go }) {
  const [items,setItems]=useState([]); const [q,setQ]=useState(""); const [form,setForm]=useState(""); const [busy,setBusy]=useState(false);
  const load=()=>apiFetch("/projects/").then(r=>r.json()).then(d=>setItems(Array.isArray(d)?d:d.results||[]));
  useEffect(load,[]);
  const filtered=useMemo(()=>items.filter(p=>(p.title||"").toLowerCase().includes(q.toLowerCase())),[items,q]);
  const create=async()=>{if(!form.trim())return;setBusy(true);await apiFetch("/projects/",{method:"POST",body:JSON.stringify({title:form,category:"General",status:"Planning",priority:"medium",tags:[],stack:[]})});setForm("");setBusy(false);load();};
  return <div className="page"><div className="hero-row"><div><div className="eyebrow">WORKSPACE / PROJECTS</div><h1>Projects</h1><p>Projects are the source of truth for execution, context and collaboration.</p></div></div>
    <div className="toolbar"><div className="global-search large"><span>⌕</span><input value={q} onChange={e=>setQ(e.target.value)} placeholder="Filter projects..." /></div><div className="inline-create"><input value={form} onChange={e=>setForm(e.target.value)} placeholder="Create project..." onKeyDown={e=>e.key==="Enter"&&create()}/><button className="primary" onClick={create}>{busy?"...":"CREATE"}</button></div></div>
    <div className="cards-grid projects-grid">{filtered.map(p=><button className="project-card large-card" key={p.id} onClick={()=>go(`/projects/${p.id}`)}><div className="card-top"><span>{p.category}</span><i>{p.priority}</i></div><b>{p.title}</b><small>{p.description||"Workspace ready for context."}</small><div className="bar"><i style={{width:`${p.progress||0}%`}} /></div><div className="card-meta"><span>{p.status}</span><strong>{p.progress||0}%</strong></div></button>)}</div>
  </div>;
}

function ProjectDetail({ id, go }) {
  const [project,setProject]=useState(null); const [tasks,setTasks]=useState([]); const [comments,setComments]=useState([]); const [comment,setComment]=useState(""); const [tab,setTab]=useState("overview");
  const load=()=>Promise.all([apiFetch(`/projects/${id}/`).then(r=>r.json()),apiFetch(`/tasks/?project=${id}`).then(r=>r.json()),apiFetch(`/comments/?project=${id}`).then(r=>r.json())]).then(([p,t,c])=>{setProject(p);setTasks(Array.isArray(t)?t:[]);setComments(c||[]);});
  useEffect(load,[id]);
  const addComment=async()=>{if(!comment.trim())return;await apiFetch("/comments/",{method:"POST",body:JSON.stringify({project:id,body:comment})});setComment("");load();};
  if(!project)return <div className="page"><div className="loading">LOADING PROJECT...</div></div>;
  return <div className="page"><button className="back" onClick={()=>go("/projects")}>← PROJECTS</button><div className="project-hero"><div><div className="eyebrow">{project.category} / {project.status}</div><h1>{project.title}</h1><p>{project.description||"No project description."}</p></div><button className="primary" onClick={()=>go("/ide")}>OPEN IN IDE ⌘</button></div>
    <div className="tabs">{["overview","tasks","collaboration"].map(t=><button className={tab===t?"selected":""} onClick={()=>setTab(t)} key={t}>{t}</button>)}</div>
    {tab==="overview"&&<div className="dashboard-grid"><section className="panel"><span className="panel-kicker">PROJECT SIGNAL</span><h2>{project.progress||0}% delivered</h2><div className="big-bar"><i style={{width:`${project.progress||0}%`}}/></div><div className="metric-row"><span>Status <b>{project.status}</b></span><span>Priority <b>{project.priority}</b></span><span>Tasks <b>{project.task_count||tasks.length}</b></span></div></section><section className="panel"><span className="panel-kicker">LIVE DISCUSSION</span><div className="comment-box">{comments.slice(0,6).map(c=><div className="comment" key={c.id}><b>{c.author_name}</b><span>{c.body}</span></div>)}{!comments.length&&<div className="empty">Start the project conversation.</div>}</div><div className="comment-input"><input value={comment} onChange={e=>setComment(e.target.value)} onKeyDown={e=>e.key==="Enter"&&addComment()} placeholder="Write a project update..." /><button onClick={addComment}>POST</button></div></section></div>}
    {tab==="tasks"&&<section className="panel"><div className="panel-head"><h2>Execution queue</h2><span>{tasks.length} tasks</span></div>{tasks.map(t=><div className="task-row" key={t.id}><span className={`task-dot ${t.status}`}/><b>{t.title}</b><small>{t.status} · {t.priority}</small>{t.assignee&&<i>@{t.assignee}</i>}</div>)}{!tasks.length&&<div className="empty">No tasks yet.</div>}</section>}
    {tab==="collaboration"&&<Collab projectId={id}/>}
  </div>;
}

function Collab({projectId}) {
  const [members,setMembers]=useState([]); const [identifier,setIdentifier]=useState(""); const [role,setRole]=useState("developer"); const [orgs,setOrgs]=useState([]);
  const load=()=>Promise.all([apiFetch(`/projects/${projectId}/collaborators/`).then(r=>r.json()),apiFetch("/organizations/").then(r=>r.json())]).then(([m,o])=>{setMembers(m);setOrgs(o)});
  useEffect(load,[projectId]);
  const add=async()=>{if(!identifier)return;await apiFetch(`/projects/${projectId}/collaborators/`,{method:"POST",body:JSON.stringify({username:identifier,email:identifier,role})});setIdentifier("");load();};
  return <div className="dashboard-grid"><section className="panel"><div className="panel-head"><h2>Project members</h2><span>{members.length}</span></div><div className="member-list">{members.map(m=><div className="member"><span className="avatar">{m.username[0].toUpperCase()}</span><div><b>{m.full_name}</b><small>@{m.username} · {m.role}</small></div></div>)}</div><div className="invite-row"><input value={identifier} onChange={e=>setIdentifier(e.target.value)} placeholder="Username or email"/><select value={role} onChange={e=>setRole(e.target.value)}><option>developer</option><option>admin</option><option>viewer</option></select><button className="primary" onClick={add}>ADD</button></div></section><section className="panel"><span className="panel-kicker">ORGANIZATION LAYER</span><h2>{orgs.length} organization(s)</h2><p>Teams, roles, plans and API access are persisted in the platform layer.</p><button className="ghost" onClick={()=>window.location.href="/team"}>OPEN TEAM CONTROL →</button></section></div>;
}

function AI() {  const [conversations,setConversations]=useState([]);
  const [conversation,setConversation]=useState(null);
  const [messages,setMessages]=useState([]);
  const [input,setInput]=useState("");
  const [busy,setBusy]=useState(false);
  const [mode,setMode]=useState("chat");
  const [action,setAction]=useState("");
  const [project,setProject]=useState("");
  const [projects,setProjects]=useState([]);
  const [workspace,setWorkspace]=useState("");
  const [workspaces,setWorkspaces]=useState([]);
  const [health,setHealth]=useState(null);
  const [error,setError]=useState("");
  const bottom=useRef(null);

  useEffect(()=>{
    apiFetch("/ai/conversations/").then(r=>r.json()).then(d=>setConversations(Array.isArray(d)?d:d.results||[])).catch(()=>{});
    apiFetch("/projects/").then(r=>r.json()).then(d=>setProjects(Array.isArray(d)?d:d.results||[])).catch(()=>{});
    apiFetch("/ide/workspaces/").then(r=>r.json()).then(d=>setWorkspaces(Array.isArray(d)?d:[])).catch(()=>{});
    apiFetch("/ai/health/").then(r=>r.json()).then(setHealth).catch(()=>{});
  },[]);
  useEffect(()=>{bottom.current?.scrollIntoView({behavior:"smooth"});},[messages]);

  const open=async id=>{
    const c=conversations.find(x=>x.id===id);
    setConversation(c); setError("");
    try{
      const r=await apiFetch(`/ai/conversations/${id}/messages/`);
      setMessages(await r.json());
      if(c?.project)setProject(String(c.project));
    }catch(e){setError(e.message);}
  };

  const send=async()=>{
    if(!input.trim()||busy)return;
    const text=input.trim(); setInput(""); setError("");
    setMessages(m=>[...m,{role:"user",content:text}]); setBusy(true);
    try{
      const body={message:text,conversation:conversation?.id||undefined,project:project||undefined,workspace:workspace||undefined};
      let endpoint="/ai/chat/";
      if(mode==="agent"){endpoint="/ai/agent/";}
      if(mode==="action"){endpoint="/ai/actions/"; body.action=action||"project"; body.input=text; delete body.message;}
      const r=await apiFetch(endpoint,{method:"POST",body:JSON.stringify(body)});
      const d=await r.json();
      if(!r.ok)throw new Error(formatApiError(d,"Developer OS Intelligence failed."));
      const answer=d.message?.content||d.answer||"No response returned.";
      setConversation(d.conversation||conversation);
      setMessages(m=>[...m,{role:"assistant",content:answer}]);
      if(d.conversation&&!conversation)setConversations(x=>[d.conversation,...x]);
    }catch(e){
      setError(e.message);
      setMessages(m=>[...m,{role:"assistant",content:`Engine error: ${e.message}`}]);
    }finally{setBusy(false);}
  };

  const quick=(text)=>{setInput(text);setMode("action");setAction("project");};

  return <div className="page ai-page">
    <div className="hero-row">
      <div><div className="eyebrow">INTELLIGENCE LAYER / CONTROL PLANE</div><h1>Developer Intelligence</h1><p>Think → Plan → Execute → Verify. Context-aware intelligence for architecture, delivery, code and project operations.</p></div>
      <div className="ai-status"><i/> {health?.status==="ready"?"INTELLIGENCE READY":"INTELLIGENCE DEGRADED"}<small>{project?"PROJECT CONTEXT":"GLOBAL CONTEXT"} · {mode.toUpperCase()}</small></div>
    </div>
    <div className="ai-toolbar panel">
      <label>PROJECT <select value={project} onChange={e=>setProject(e.target.value)}><option value="">Workspace-wide</option>{projects.map(p=><option key={p.id} value={p.id}>{p.title}</option>)}</select></label>
      <label>WORKSPACE <select value={workspace} onChange={e=>setWorkspace(e.target.value)}><option value="">Auto context</option>{workspaces.map(w=><option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
      <div className="ai-modes"><button className={mode==="chat"?"active":""} onClick={()=>setMode("chat")}>ASK <small>REASON</small></button><button className={mode==="agent"?"active":""} onClick={()=>setMode("agent")}>AGENT <small>EXECUTE</small></button><button className={mode==="action"?"active":""} onClick={()=>setMode("action")}>MANAGE <small>OPERATE</small></button></div>
      {mode==="action"&&<select value={action} onChange={e=>setAction(e.target.value)}><option value="">Choose operation</option><option value="project">PROJECT PLAN</option><option value="sprint">NEXT SPRINT</option><option value="risk">DELIVERY RISKS</option><option value="review">CODE REVIEW</option><option value="tests">TEST PLAN</option><option value="debug">DEBUG</option><option value="plan">IMPLEMENTATION PLAN</option><option value="explain">EXPLAIN</option></select>}
    </div>
    <div className="ai-layout">
      <aside className="panel ai-history">
        <button className="primary wide" onClick={()=>{setConversation(null);setMessages([]);setError("");}}>+ NEW THREAD</button>
        <div className="history-label">THREADS</div>
        {conversations.map(c=><button className={conversation?.id===c.id?"history-on":""} onClick={()=>open(c.id)} key={c.id}>{c.title}</button>)}
      </aside>
      <section className="panel chat">
        <div className="chat-head"><span>✦</span><div><b>{conversation?.title||"Workspace Intelligence"}</b><small>Projects · Tasks · Notes · IDE · Repository · Delivery</small></div></div>
        <div className="messages">
          {!messages.length&&<div className="ai-welcome"><div className="ai-orb">✦</div><h2>Operate the project, not just the conversation.</h2><p>Use real project context to plan milestones, identify risks, review code, generate tests, inspect the workspace and verify changes.</p><div className="prompt-chips">{["Analyze my project and give me the next milestone","Find blockers and delivery risks","Create a practical sprint plan","Review my workspace architecture"].map(x=><button onClick={()=>quick(x)} key={x}>{x}</button>)}</div></div>}
          {messages.map((m,i)=><div className={`message ${m.role}`} key={m.id||i}><span>{m.role==="user"?"YOU":"DOS"}</span><div>{m.content}</div></div>)}
          <div ref={bottom}/>
        </div>
        {error&&<div className="error">{error}</div>}
        <div className="chat-input"><textarea value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send();}}} placeholder={mode==="agent"?"Describe the engineering problem; the agent will inspect evidence...":"Ask, plan or manage your Developer OS workspace..."} /><button className="primary" disabled={busy} onClick={send}>{busy?"THINKING...":"RUN →"}</button></div>
      </section>
    </div>
  </div>;
}

function Explore() {
  const [tab,setTab]=useState("tools");
  const [q,setQ]=useState("");
  const [category,setCategory]=useState("All");
  const [sort,setSort]=useState("recommended");
  const [activeTag,setActiveTag]=useState("");
  const [tools,setTools]=useState([]);
  const [workflows,setWorkflows]=useState([]);
  const [resources,setResources]=useState([]);
  const [favorites,setFavorites]=useState([]);
  const [workspaces,setWorkspaces]=useState([]);
  const [busy,setBusy]=useState(true);
  const [aiBusy,setAiBusy]=useState(false);
  const [aiResult,setAiResult]=useState("");
  const [error,setError]=useState("");
  const [toast,setToast]=useState("");
  const [savedOnly,setSavedOnly]=useState(false);
  const [pageSize,setPageSize]=useState(12);

  const load=async()=>{
    setBusy(true); setError("");
    try{
      const [t,w,r,f,ws]=await Promise.all([
        apiFetch(`/tools/?q=${encodeURIComponent(q)}`).then(x=>x.json()),
        apiFetch(`/workflows/?q=${encodeURIComponent(q)}`).then(x=>x.json()),
        apiFetch(`/resources/?q=${encodeURIComponent(q)}`).then(x=>x.json()),
        apiFetch("/favorites/").then(x=>x.json()),
        apiFetch("/ide/workspaces/").then(x=>x.json()),
      ]);
      const arr=x=>Array.isArray(x)?x:(x?.results||[]);
      setTools(arr(t)); setWorkflows(arr(w)); setResources(arr(r)); setFavorites(arr(f)); setWorkspaces(arr(ws));
    }catch(e){setError(e.message)}
    finally{setBusy(false)}
  };
  useEffect(()=>{const t=setTimeout(()=>load(),220);return()=>clearTimeout(t)},[q]);
  useEffect(()=>{const fn=e=>{if(e.key==="/"&&!["INPUT","TEXTAREA"].includes(document.activeElement?.tagName)){e.preventDefault();document.querySelector(".explore-search")?.focus()}};window.addEventListener("keydown",fn);return()=>window.removeEventListener("keydown",fn)},[]);

  const toggle=async tool=>{
    const saved=favorites.some(f=>f.tool?.name?.toLowerCase()===tool.name.toLowerCase());
    try{
      if(saved){
        await apiFetch(`/favorites/?tool_name=${encodeURIComponent(tool.name)}`,{method:"DELETE"});
        setFavorites(favorites.filter(f=>f.tool?.name?.toLowerCase()!==tool.name.toLowerCase()));
      }else{
        const r=await apiFetch("/favorites/",{method:"POST",body:JSON.stringify({tool_name:tool.name})});
        const d=await r.json(); if(!r.ok)throw Error(d.error||"Favorite failed");
        setFavorites([...favorites,d]);
      }
    }catch(e){setError(e.message)}
  };

  const addToWorkspace=async tool=>{
    try{
      const name=`${tool.name} Lab`;
      const body={name,language:"javascript",framework:tool.category||"",runtime:"node",package_manager:"npm",files:{"README.md":`# ${name}\n\nWorkspace created from Developer OS Explore.\n\nTool: ${tool.name}\nCategory: ${tool.category||"General"}\n\n## Next move\nStart building.\n`,"package.json":JSON.stringify({name:name.toLowerCase().replace(/[^a-z0-9]+/g,"-"),private:true,version:"0.1.0",scripts:{dev:"vite",build:"vite build"}},null,2)}};
      const r=await apiFetch("/ide/workspaces/",{method:"POST",body:JSON.stringify(body)});
      const d=await r.json(); if(!r.ok)throw Error(d.error||"Workspace creation failed");
      setWorkspaces(x=>[d,...x]); setToast(`Workspace “${name}” created`); setTimeout(()=>setToast(""),2600);
    }catch(e){setError(e.message)}
  };

  const aiDiscover=async()=>{
    if(aiBusy)return; setAiBusy(true); setAiResult(""); setError("");
    try{
      const prompt=`You are the Developer OS Discovery Engine. Based on this catalog and user signal, recommend a concise stack for the next build. Search: "${q||"none"}". Category: ${category}. Favorites: ${favorites.map(f=>f.tool?.name).filter(Boolean).slice(0,12).join(", ")||"none"}. Available tools: ${tools.slice(0,20).map(t=>t.name).join(", ")}. Return 3 practical recommendations with one-line reasons and one next action. Do not invent tools outside the supplied catalog.`;
      const r=await apiFetch("/ai/chat/",{method:"POST",body:JSON.stringify({message:prompt})});
      const d=await r.json(); if(!r.ok)throw Error(d.error||d.detail||"AI Discovery unavailable");
      setAiResult(d.message?.content||d.answer||"No recommendation returned.");
    }catch(e){setError(e.message)}
    finally{setAiBusy(false)}
  };

  const categories=["All",...new Set(tools.map(t=>t.category||t.tag).filter(Boolean))];
  const tags=[...new Set(tools.flatMap(t=>Array.isArray(t.features)?t.features:[]).filter(Boolean))].slice(0,14);
  const favoriteNames=new Set(favorites.map(f=>f.tool?.name?.toLowerCase()).filter(Boolean));
  const score=t=>{
    const text=`${t.name} ${t.description||""} ${t.category||""} ${(t.features||[]).join(" ")}`.toLowerCase();
    const queryScore=q?((q.toLowerCase().split(/\s+/).filter(Boolean).filter(x=>text.includes(x)).length)*12):0;
    const favScore=favoriteNames.has(String(t.name).toLowerCase())?28:0;
    const ratingScore=Number(t.rating||0)*7;
    const featureScore=(t.features||[]).length*2;
    return queryScore+favScore+ratingScore+featureScore;
  };
  const filteredTools=tools.filter(t=>(category==="All"||(t.category||t.tag)===category)&&(!activeTag||(t.features||[]).includes(activeTag))&&(!savedOnly||favoriteNames.has(String(t.name).toLowerCase())));
  const sortedTools=[...filteredTools].sort((a,b)=>{
    if(sort==="rating")return Number(b.rating||0)-Number(a.rating||0);
    if(sort==="name")return String(a.name).localeCompare(String(b.name));
    if(sort==="newest")return new Date(b.created_at||0)-new Date(a.created_at||0);
    return score(b)-score(a);
  });
  const data=tab==="tools"?sortedTools:tab==="workflows"?workflows:resources;
  const total=tools.length+workflows.length+resources.length;
  const visibleTools=sortedTools.slice(0,pageSize);
  const visibleWorkflows=workflows.slice(0,pageSize);
  const visibleResources=resources.slice(0,pageSize);
  const hasFilters=Boolean(q.trim()||category!=="All"||activeTag||savedOnly);
  const currentCount=data.length;


  return <div className="page explore-page explore-modern">
    <section className="explore-hero">
      <div className="explore-hero-glow"/>
      <div className="explore-hero-copy">
        <div className="eyebrow">DEVELOPER OS / INTELLIGENT DISCOVERY</div>
        <h1>Explore <span>your next advantage.</span></h1>
        <p>A living developer marketplace that learns your stack, surfaces useful signals and turns discovery into an actual workspace.</p>
        <div className="explore-hero-actions">
          <button className="primary" onClick={()=>document.querySelector(".explore-search")?.focus()}>⌕ DISCOVER</button>
          <button className="ghost explore-ai-btn" onClick={aiDiscover}>{aiBusy?"◌ THINKING…":"✦ ASK DOS AI"}</button>
          <span className="explore-shortcut">Press <kbd>/</kbd> to search</span>
        </div>
      </div>
      <div className="explore-orbit" aria-hidden="true"><div className="orbit-core">✦</div><i/><i/><i/></div>
    </section>

    <section className="explore-command panel">
      <div className="explore-search-wrap"><span>⌕</span><input className="explore-search" value={q} onChange={e=>{setQ(e.target.value);setPageSize(12)}} placeholder="Find a tool, workflow, stack or idea…" aria-label="Search Explore catalog"/>{q&&<button className="explore-clear" onClick={()=>setQ("")}>×</button>}<kbd>/</kbd></div>
      <div className="explore-tabs">{[["tools","TOOLS","⌘"],["workflows","WORKFLOWS","↗"],["resources","RESOURCES","◈"]].map(([id,label,icon])=><button className={tab===id?"active":""} onClick={()=>{setTab(id);setPageSize(12);if(id!=="tools"){setCategory("All");setActiveTag("");setSavedOnly(false)}}} key={id}><span>{icon}</span>{label}<b>{id==="tools"?tools.length:id==="workflows"?workflows.length:resources.length}</b></button>)}</div>
    </section>

    <div className="explore-insights">
      <div><span>DISCOVERY INDEX</span><strong>{total||"—"}</strong><small>catalog signals</small></div>
      <div><span>PERSONAL SIGNAL</span><strong>{favoriteNames.size}</strong><small>saved preferences</small></div>
      <div><span>WORKSPACES</span><strong>{workspaces.length}</strong><small>ready to build</small></div>
      <div className="explore-motto"><span>DISCOVER.</span><strong>COMPOSE. BUILD. SHIP.</strong></div>
    </div>

    {aiResult&&<section className="explore-ai-panel"><div className="explore-ai-head"><div><span>✦ DOS INTELLIGENCE</span><strong>Discovery Brief</strong></div><button onClick={()=>setAiResult("")}>×</button></div><p>{aiResult}</p></section>}
    {error&&<div className="error">{error}</div>}
    {toast&&<div className="explore-toast">✓ {toast}</div>}

    <section className="explore-controls">
      {tab==="tools"&&<div className="explore-categories" role="group" aria-label="Tool category">{categories.map(x=><button type="button" key={x} className={category===x?"active":""} aria-pressed={category===x} onClick={()=>{setCategory(x);setPageSize(12)}}>{x}</button>)}</div>}
      <div className="explore-control-actions">{tab==="tools"&&<button type="button" className={savedOnly?"active":""} aria-pressed={savedOnly} onClick={()=>{setSavedOnly(v=>!v);setPageSize(12)}}>♥ Saved only</button>}{tab==="tools"&&<label className="explore-sort-label">SORT <select value={sort} onChange={e=>{setSort(e.target.value);setPageSize(12)}} aria-label="Sort results"><option value="recommended">✦ Recommended</option><option value="rating">★ Highest rated</option><option value="newest">◷ Newest</option><option value="name">A–Z</option></select></label>}<button type="button" className="explore-refresh" onClick={()=>void load()} disabled={busy} aria-label="Refresh catalog" title="Refresh catalog">{busy?"◌":"↻"} <span>{busy?"Loading":"Refresh"}</span></button>{hasFilters&&<button type="button" className="explore-reset" onClick={()=>{setQ("");setCategory("All");setActiveTag("");setSavedOnly(false);setSort("recommended");setPageSize(12)}}>Clear filters</button>}</div>
    </section>

    {tab==="tools"&&tags.length>0&&<div className="explore-tags"><span>TAGS</span>{tags.map(x=><button key={x} className={activeTag===x?"active":""} onClick={()=>{setActiveTag(activeTag===x?"":x);setPageSize(12)}}>#{x}</button>)}</div>}

    <div className="explore-results-line" aria-live="polite"><div><strong>{busy?"Updating catalog…":currentCount.toLocaleString()+" results"}</strong><span>{tab==="tools"?"Developer tools":tab==="workflows"?"Engineering workflows":"Knowledge resources"}{hasFilters?" · filtered":" · full catalog"}</span></div><span>{tab.toUpperCase()} / {Math.min(pageSize,currentCount)} OF {currentCount}</span></div>
    {busy?<section className="explore-grid-pro">{[1,2,3,4,5,6].map(i=><div className="explore-skeleton" key={i}><i/><i/><i/><i/></div>)}</section>:
      <section className="explore-grid-pro">
        {tab==="tools"&&visibleTools.map((t,index)=>{
          const saved=favoriteNames.has(String(t.name).toLowerCase());
          return <article className={`explore-card ${index<3?"trending-card":""}`} key={t.id}>
            {index<3&&<div className="trending-badge">↗ TRENDING SIGNAL</div>}
            <div className="explore-card-top"><span className="explore-tag">{t.category||t.tag||"DEVELOPER TOOL"}</span><button className={saved?"saved":""} onClick={()=>toggle(t)} aria-label={saved?`Remove ${t.name} from favorites`:`Save ${t.name}`}>{saved?"♥":"♡"}</button></div>
            <div className="explore-icon">⌘</div><h3>{t.name}</h3><p>{t.description||"A developer capability ready for your next build."}</p>
            <div className="explore-card-tags">{(t.features||[]).slice(0,3).map(x=><span key={x}>#{x}</span>)}</div>
            <div className="explore-meta"><span>★ {t.rating??"—"}</span><span>{saved?"PERSONAL MATCH":"DISCOVERY MATCH"}</span></div>
            <div className="explore-card-footer"><button onClick={()=>addToWorkspace(t)}>＋ ADD TO WORKSPACE</button><i>→</i></div>
          </article>
        })}
        {tab==="workflows"&&visibleWorkflows.map(w=><article className="explore-card workflow-card" key={w.id}><div className="explore-card-top"><span className="explore-tag">{w.level||"WORKFLOW"}</span><span>{w.duration||"PLAYBOOK"}</span></div><div className="explore-icon">↗</div><h3>{w.title}</h3><p>{w.summary||"A repeatable engineering playbook for moving from intent to delivery."}</p><ol>{(Array.isArray(w.steps)?w.steps:[]).slice(0,3).map((step,i)=><li key={i}>{typeof step==="string"?step:JSON.stringify(step)}</li>)}</ol><div className="explore-card-footer"><button onClick={()=>setToast(`Workflow “${w.title}” selected`)}>START PLAYBOOK</button><i>→</i></div></article>)}
        {tab==="resources"&&visibleResources.map(r=><article className="explore-card" key={r.id}><div className="explore-card-top"><span className="explore-tag">{r.resource_type||"RESOURCE"}</span><span>{r.category||"KNOWLEDGE"}</span></div><div className="explore-icon">◈</div><h3>{r.title}</h3><p>{r.description||"Developer knowledge for sharper decisions and better builds."}</p>{r.link?<a className="explore-link" href={r.link} target="_blank" rel="noreferrer">OPEN RESOURCE <b>↗</b></a>:<div className="explore-card-footer"><span>INTERNAL RESOURCE</span><i>→</i></div>}</article>)}
        {!data.length&&<div className="explore-empty"><div>⌕</div><h3>No signal found.</h3><p>Change the search, category or tag and let the discovery engine try again.</p><button className="ghost" onClick={()=>{setQ("");setCategory("All");setActiveTag("")}}>RESET DISCOVERY</button></div>}
      </section>}
    {!busy&&data.length>pageSize&&<div className="explore-load-more"><span>Showing {Math.min(pageSize,data.length)} of {data.length}</span><button type="button" className="ghost" onClick={()=>setPageSize(v=>Math.min(v+12,data.length))}>LOAD MORE <span>↓</span></button></div>}
  </div>;
}
function SearchPage() {
  const params=new URLSearchParams(window.location.search); const [q,setQ]=useState(params.get("q")||""); const [results,setResults]=useState([]);
  useEffect(()=>{if(q)apiFetch(`/platform/search/?q=${encodeURIComponent(q)}`).then(r=>r.json()).then(d=>setResults(d.results||[])).catch(()=>{});},[q]);
  return <div className="page"><div className="hero-row"><div><div className="eyebrow">KNOWLEDGE GRAPH / SEARCH</div><h1>Universal Search</h1><p>Projects, tasks, notes, snippets and developer catalog — one index.</p></div></div><div className="search-big"><span>⌕</span><input autoFocus value={q} onChange={e=>setQ(e.target.value)} placeholder="Search your entire Developer OS..." /></div><div className="results">{results.map((r,i)=><div className="result" key={`${r.type}-${r.id}-${i}`}><span>{r.type}</span><div><b>{r.title}</b><p>{r.subtitle}</p></div><strong>→</strong></div>)}{q&&!results.length&&<div className="empty">No results for “{q}”.</div>}</div></div>;
}

function Team() {
  const [orgs,setOrgs]=useState([]); const [members,setMembers]=useState([]); const [invites,setInvites]=useState([]);
  const [name,setName]=useState(""); const [selected,setSelected]=useState(null); const [email,setEmail]=useState(""); const [role,setRole]=useState("developer"); const [token,setToken]=useState(""); const [message,setMessage]=useState(""); const [teamSeats,setTeamSeats]=useState(1); const [teamBusy,setTeamBusy]=useState(false); const [annual,setAnnual]=useState(false);
  const load=()=>apiFetch("/organizations/").then(r=>r.json()).then(d=>{setOrgs(d);if(d[0]&&!selected)setSelected(d[0])});
  const loadOrg=async org=>{setSelected(org);const [m,i]=await Promise.all([apiFetch(`/organizations/${org.id}/members/`).then(r=>r.json()),apiFetch(`/organizations/${org.id}/invites/`).then(r=>r.json())]);setMembers(m);setInvites(i)};
  useEffect(load,[]);
  useEffect(()=>{if(selected)loadOrg(selected).catch(()=>{setMembers([]);setInvites([])});},[selected?.id]);
  const create=async()=>{if(!name)return;await apiFetch("/organizations/",{method:"POST",body:JSON.stringify({name})});setName("");load();};
  const invite=async()=>{if(!selected||!email)return;const r=await apiFetch(`/organizations/${selected.id}/invites/`,{method:"POST",body:JSON.stringify({email,role})});const d=await r.json();if(!r.ok){setMessage(d.error||"Invite failed");return;}setEmail("");setMessage(`Invite created for ${d.email}. Token: ${d.token}`);loadOrg(selected);};
  const accept=async()=>{if(!token)return;const r=await apiFetch("/organizations/invites/accept/",{method:"POST",body:JSON.stringify({token})});const d=await r.json();setMessage(r.ok?"Invitation accepted.":(d.error||"Unable to accept invitation."));if(r.ok)load();};
  const teamCheckout=async()=>{if(!selected)return;setTeamBusy(true);try{const quantity=Math.max(members.length||1,Math.min(5000,Number(teamSeats)||1));const r=await apiFetch("/organizations/"+selected.id+"/billing/checkout/",{method:"POST",body:JSON.stringify({plan:"team",quantity,billing_cycle:annual?"annual":"monthly"})});const d=await r.json();if(!r.ok)throw new Error(d.error||"Team billing failed.");if(d.checkout_url){window.location.assign(d.checkout_url);return;}setMessage("Team billing updated for "+quantity+" seat(s).");}catch(e){setMessage(e.message)}finally{setTeamBusy(false)}};
  return <div className="page"><div className="hero-row"><div><div className="eyebrow">COLLABORATION FABRIC</div><h1>Team Control</h1><p>Organizations, roles, invitations, membership and governance.</p></div></div><div className="dashboard-grid"><section className="panel"><div className="panel-head"><h2>Organizations</h2></div><div className="org-create"><input value={name} onChange={e=>setName(e.target.value)} placeholder="Organization name"/><button className="primary" onClick={create}>CREATE</button></div>{orgs.map(o=><button className={`org-row ${selected?.id===o.id?"selected":""}`} onClick={()=>loadOrg(o)} key={o.id}><b>{o.name}</b><span>{o.plan}</span></button>)}{!orgs.length&&<div className="empty">Create your team space.</div>}<div className="panel-head"><h2>Accept invitation</h2></div><div className="invite-row"><input value={token} onChange={e=>setToken(e.target.value)} placeholder="Invitation token"/><button className="primary" onClick={accept}>ACCEPT</button></div></section><section className="panel"><div className="panel-head"><h2>{selected?.name||"Select organization"}</h2><span>{members.length} members</span></div>{selected&&<div className="invite-row"><input value={email} onChange={e=>setEmail(e.target.value)} placeholder="Invite email"/><select value={role} onChange={e=>setRole(e.target.value)}><option value="developer">developer</option><option value="admin">admin</option><option value="viewer">viewer</option></select><button className="primary" onClick={invite}>INVITE</button></div>}{message&&<div className="secret-key">{message}</div>}{members.map(m=><div className="member" key={m.id}><span className="avatar">{m.username[0].toUpperCase()}</span><div><b>{m.username}</b><small>{m.email} · {m.role}</small></div></div>)}<div className="panel-head"><h2>Pending invites</h2><span>{invites.length}</span></div>{selected&&<div className="team-billing-panel"><strong>Team: $15/user/month · Enterprise: from $299/month</strong><p>Use organization billing for per-seat checkout and enterprise controls.</p><div className="billing-switch"><button className={!annual?"active":""} onClick={()=>setAnnual(false)}>MONTHLY</button><button className={annual?"active":""} onClick={()=>setAnnual(true)}>ANNUAL</button></div><input type="number" min={Math.max(1,members.length)} max="5000" value={teamSeats} onChange={e=>setTeamSeats(e.target.value)} aria-label="Team seats"/><button className="primary" disabled={teamBusy} onClick={teamCheckout}>{teamBusy?"OPENING…":"TEAM CHECKOUT"}</button></div>}{invites.map(i=><div className="key-row" key={i.id}><span>{i.email}</span><small>{i.role} · expires {new Date(i.expires_at).toLocaleDateString()}</small></div>)}</section></div></div>;
}

function Audit() {
  const [items,setItems]=useState([]);
  useEffect(()=>{apiFetch("/audit/").then(r=>r.json()).then(setItems).catch(()=>setItems([]));},[]);
  return <div className="page"><div className="hero-row"><div><div className="eyebrow">OPERATIONS / GOVERNANCE</div><h1>Audit Log</h1><p>Security-relevant workspace, organization and billing events.</p></div></div><section className="panel"><div className="panel-head"><h2>Recent events</h2><span>{items.length}</span></div>{items.map(x=><div className="result" key={x.id}><span>{x.action}</span><div><b>{x.target_type} {x.target_id}</b><p>{new Date(x.created_at).toLocaleString()}</p></div><strong>→</strong></div>)}{!items.length&&<div className="empty">No audit events yet.</div>}</section></div>;
}

function Billing() {
  const [sub,setSub]=useState(null); const [usage,setUsage]=useState(null); const [keys,setKeys]=useState([]);
  const [newKey,setNewKey]=useState(""); const [error,setError]=useState(""); const [pricing,setPricing]=useState(null);
  const [annual,setAnnual]=useState(false);
  const load=()=>Promise.all([apiFetch("/subscription/"),apiFetch("/usage/"),apiFetch("/api-keys/"),apiFetch("/pricing/")]).then(async rs=>{
    const ds=await Promise.all(rs.map(r=>r.json()));setSub(ds[0]);setUsage(ds[1]);setKeys(ds[2]);setPricing(ds[3]);
  }).catch(e=>setError(e.message));
  useEffect(load,[]);
  const upgrade=async plan=>{
    if(plan==="team"){window.location.assign("/team");return;}
    if(plan==="enterprise"){
      try{const r=await apiFetch("/support-tickets/",{method:"POST",body:JSON.stringify({subject:"Enterprise plan request",body:"I want to discuss Developer OS Enterprise pricing, security, governance, deployment and support requirements.",priority:"high"})});const d=await r.json();if(!r.ok)throw new Error(d.error||"Unable to open enterprise request.");setError("Enterprise request submitted. Support will follow up.");}catch(e){setError(e.message)}return;
    }
    try{const r=await apiFetch("/subscription/",{method:"POST",body:JSON.stringify({plan,billing_cycle:annual?"annual":"monthly"})});const d=await r.json();if(!r.ok)throw new Error(d.error||"Billing request failed");if(d.checkout_url){window.location.assign(d.checkout_url);return;}load();}
    catch(e){setError(e.message)}
  };
  const portal=async()=>{const r=await apiFetch("/billing/portal/",{method:"POST"});const d=await r.json();if(d.url)window.location.assign(d.url);else setError(d.error||"Portal unavailable")};
  const createKey=async()=>{const r=await apiFetch("/api-keys/",{method:"POST",body:JSON.stringify({name:"Developer OS CLI"})});const d=await r.json();if(!r.ok){setError(d.error||"Key creation failed");return;}setNewKey(d.key);load();};
  const plans=pricing?.plans ? Object.entries(pricing.plans).map(([id,p])=>{
    const monthly=Number(p.monthly_usd||0), annual=Number(p.annual_usd||0);
    const display=annual&&annual? (annual ? "$"+(annual/12).toFixed(2)+"/mo" : "$0") : (monthly===0?"$0":"$"+monthly+(p.starting_at?"+":"")+"/mo");
    return {id,p,price:annual&&annual ? (annual ? "$"+annual+"/yr" : "$0") : (monthly===0?"$0":"$"+monthly+(p.starting_at?"+":"")),display};
  }) : [];
  return <div className="page">
    <div className="hero-row"><div><div className="eyebrow">SAAS CONTROL PLANE</div><h1>Plans & Usage</h1><p>Choose the workspace model that matches how you build: solo, professional, team, or enterprise.</p></div><div className="hero-actions"><button className="ghost" onClick={portal}>MANAGE BILLING ↗</button></div></div>
    {error&&<div className="error">{error}</div>}
    <div className="billing-switch"><button className={!annual?"active":""} onClick={()=>setAnnual(false)}>MONTHLY</button><button className={annual?"active":""} onClick={()=>setAnnual(true)}>ANNUAL <small>SAVE 2 MONTHS</small></button></div>
    <div className="plan-grid">{plans.map(({id,p,price,display})=><div className={"plan "+(sub?.plan===id?"current ":"")+(p.recommended?"recommended":"")} key={id}>
      {p.recommended&&<span className="plan-badge">RECOMMENDED</span>}
      <span>{p.label?.toUpperCase()||id.toUpperCase()}</span><h2>{annual&&p.annual_usd?display:price}</h2>
      <strong>{p.audience||"Developer OS plan"}</strong>
      <p>{p.billing_model==="per_seat"?"Per active seat":p.billing_model==="custom"?"Custom organization contract":id==="free"?"No payment required":"Individual developer plan"}</p>
      {Array.isArray(p.features)&&<ul className="plan-features">{p.features.map(feature=><li key={feature}>✓ {feature}</li>)}</ul>}
      <button className={sub?.plan===id?"ghost":"primary"} onClick={()=>upgrade(id)}>{sub?.plan===id?"ACTIVE":id==="team"?"MANAGE TEAM":id==="enterprise"?"REQUEST ENTERPRISE":id==="free"?"USE FREE":"UPGRADE TO PRO"}</button>
    </div>)}</div>
    <section className="panel"><div className="panel-head"><div><span className="panel-kicker">METERED USAGE</span><h2>This month</h2></div><span>{usage?.plan?.toUpperCase()||"—"}</span></div>
      <div className="cards-grid">{Object.entries(usage?.metrics||{}).map(([k,v])=>{const pct=v.limit==null?0:Math.min(100,(v.used/Math.max(1,v.limit))*100);return <div className="project-card" key={k}><span>{k.replaceAll("_"," ").toUpperCase()}</span><b>{v.limit==null?(v.used+" / ∞"):(v.used+" / "+v.limit)}</b>{v.limit!=null&&<div className="bar"><i style={{width:pct+"%"}} /></div>}{v.limit!=null&&pct>=80&&<small>Approaching plan limit — upgrade when you need more capacity.</small>}</div>})}</div>
    </section>
    <section className="panel"><div className="panel-head"><div><span className="panel-kicker">DEVELOPER API</span><h2>API keys</h2></div><button className="primary" onClick={createKey}>+ CREATE KEY</button></div>
      {newKey&&<div className="secret-key"><b>Copy this key now — it will not be shown again:</b><code>{newKey}</code></div>}
      {keys.map(k=><div className="key-row" key={k.id}><code>{k.prefix}••••••••</code><span>{k.name}</span><small>{k.revoked_at?"REVOKED":"ACTIVE"}</small></div>)}
    </section>
  </div>;
}
function Profile({ onProfileUpdate }) {
  const [profile, setProfile] = useState(null);
  const [avatarFile, setAvatarFile] = useState(null);
  const [avatarPreview, setAvatarPreview] = useState("");
  const [removeAvatar, setRemoveAvatar] = useState(false);
  const [avatarError, setAvatarError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const avatarInputRef = useRef(null);

  useEffect(() => {
    let active = true;
    apiFetch("/profile/")
      .then(response => response.json())
      .then(data => {
        if (active) {
          setProfile(data);
          onProfileUpdate?.(data);
        }
      })
      .catch(() => { if (active) setSaveError("Could not load your profile. Please refresh and try again."); });
    return () => { active = false; };
  }, [onProfileUpdate]);

  useEffect(() => {
    if (!avatarFile) {
      setAvatarPreview("");
      return undefined;
    }
    const objectUrl = URL.createObjectURL(avatarFile);
    setAvatarPreview(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [avatarFile]);

  const chooseAvatar = event => {
    const file = event.target.files?.[0];
    // Clear immediately so choosing the same file again still fires change.
    event.target.value = "";
    setAvatarError("");
    if (!file) return;
    if (!new Set(["image/jpeg", "image/png", "image/webp"]).has(file.type)) {
      setAvatarError("Choose a JPEG, PNG, or WebP image.");
      event.target.value = "";
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setAvatarError("The profile photo must be 5 MB or smaller.");
      event.target.value = "";
      return;
    }
    setAvatarFile(file);
    setRemoveAvatar(false);
    setSaved(false);
    setSaveError("");
  };

  const save = async () => {
    if (!profile || saving) return;
    setSaving(true);
    setSaved(false);
    setSaveError("");
    try {
      const github = normalizeProfileLink(profile.github);
      const linkedin = normalizeProfileLink(profile.linkedin);
      const x = normalizeProfileLink(profile.x);
      const website = normalizeProfileLink(profile.website);
      const payload = new FormData();
      payload.append("full_name", profile.full_name || "");
      payload.append("first_name", profile.first_name || "");
      payload.append("last_name", profile.last_name || "");
      payload.append("email", profile.email || "");
      payload.append("bio", profile.bio || "");
      payload.append("country", profile.country || "");
      payload.append("city", profile.city || "");
      payload.append("job_title", profile.job_title || "");
      payload.append("skills", profile.skills || "");
      payload.append("timezone", profile.timezone || "");
      payload.append("github", github);
      payload.append("linkedin", linkedin);
      payload.append("x", x);
      payload.append("website", website);
      if (avatarFile) payload.append("avatar", avatarFile);
      if (removeAvatar) {
        payload.append("remove_avatar", "true");
        payload.append("avatar_url", "");
      }
      const updateResponse = await apiFetch("/profile/", { method: "PATCH", body: payload });
      const updateData = await updateResponse.json().catch(() => ({}));
      if (!updateResponse.ok) throw new Error(formatApiError(updateData, "Unable to save your profile."));
      // Re-read canonical server state rather than trusting an optimistic preview.
      const refreshed = await apiFetch("/profile/", { cache: "no-store" });
      if (!refreshed.ok) throw new Error("Profile was updated, but the refreshed profile could not be loaded.");
      const data = await refreshed.json();
      if (avatarFile && !data.avatar_url) {
        throw new Error("The server saved the profile but did not return a photo URL. Please try again.");
      }
      setProfile(data);
      onProfileUpdate?.(data);
      setAvatarFile(null);
      setRemoveAvatar(false);
      setAvatarError("");
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2200);
    } catch (error) {
      setSaveError(error.message || "Unable to save your profile. Please try again.");
    } finally {
      setSaving(false);
    }
  };

  if (!profile) return <div className="page loading">LOADING PROFILE...</div>;
  const profileName = profile.full_name || [profile.first_name, profile.last_name].filter(Boolean).join(" ") || profile.username || "Developer";
  const initials = profileName.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase() || "D";
  const displayedAvatar = avatarFile ? avatarPreview : (removeAvatar ? "" : profile.avatar_url);
  const profileChecks = [
    ["Photo", Boolean(displayedAvatar)], ["Name", Boolean(profile.full_name?.trim())],
    ["Verified email", Boolean(profile.email_verified)], ["Professional title", Boolean(profile.job_title?.trim())],
    ["Location", Boolean(profile.country?.trim())], ["Bio", Boolean(profile.bio?.trim())],
    ["Skills", Boolean(profile.skills?.trim())], ["Developer links", Boolean(profile.github || profile.website || profile.linkedin)],
  ];
  const profileScore = Math.round(profileChecks.filter(([, complete]) => complete).length / profileChecks.length * 100);
  const publicLinks = [["GitHub", profile.github], ["Website", profile.website], ["LinkedIn", profile.linkedin], ["X", profile.x]]
    .map(([label, url]) => [label, safeProfileLink(url)])
    .filter(([, url]) => Boolean(url));

  return (
    <div className="page">
      <div className="hero-row">
        <div><div className="eyebrow">YOUR SPACE / IDENTITY</div><h1>My Profile</h1><p>Your developer identity, public links and personal details — all in one dedicated place.</p></div>
      </div>
      <section className="profile-command-card" aria-label="Profile readiness and live preview">
        <div className="profile-readiness">
          <div className="profile-readiness-top"><span className="panel-kicker">PROFILE READINESS</span><strong>{profileScore}%</strong></div>
          <div className="profile-readiness-track" role="progressbar" aria-label="Profile completeness" aria-valuemin={0} aria-valuemax={100} aria-valuenow={profileScore}><span style={{ width: profileScore + "%" }} /></div>
          <p>{profileScore === 100 ? "Your developer identity is looking complete." : "Complete a few details to make collaboration and your developer identity more useful."}</p>
          <div className="profile-checklist">{profileChecks.map(([label, complete]) => <span key={label} className={complete ? "is-complete" : ""}><i>{complete ? "✓" : "○"}</i>{label}</span>)}</div>
        </div>
        <div className="profile-live-preview">
          <span className="panel-kicker">LIVE IDENTITY PREVIEW</span>
          <div className="profile-preview-person"><Avatar imageUrl={displayedAvatar} initials={initials} className="profile-preview-avatar" label={profileName + " preview"} /><div><h2>{profileName}</h2><span>@{profile.username || "developer"}</span><p>{profile.job_title || "Add a professional title"}</p></div></div>
          {(profile.city || profile.country || profile.timezone) && <div className="profile-preview-meta">{[profile.city, profile.country].filter(Boolean).join(", ")}{profile.timezone ? " · " + profile.timezone : ""}</div>}
          <p className="profile-preview-bio">{profile.bio?.trim() || "Your short bio will appear here as you edit it."}</p>
          {profile.skills?.trim() && <div className="profile-skill-chips">{profile.skills.split(",").map(skill => skill.trim()).filter(Boolean).slice(0, 8).map((skill, index) => <span key={skill + index}>{skill}</span>)}</div>}
          {publicLinks.length > 0 && <div className="profile-preview-links">{publicLinks.map(([label, url]) => <a key={label} href={url} target="_blank" rel="noopener noreferrer">{label} ↗</a>)}</div>}
        </div>
      </section>
      <section className="panel settings-form profile-settings-panel">
        <div className="profile-settings-intro">
          <div><span className="eyebrow">DEVELOPER IDENTITY</span><h2>Profile overview</h2><p>Make your profile useful for collaboration, discoverability, and future team workflows.</p></div>
          <div className={profile.email_verified ? "profile-verification verified" : "profile-verification"}>{profile.email_verified ? "✓ EMAIL VERIFIED" : "○ EMAIL UNVERIFIED"}</div>
        </div>
        <div className="profile-settings-avatar-row">
          <Avatar imageUrl={displayedAvatar} initials={initials} className="profile-settings-avatar" label={profileName + " profile photo"} />
          <div className="profile-settings-avatar-copy">
            <strong>{profileName}</strong>
            <span>@{profile.username || "developer"}</span>
            <p>JPEG, PNG, or WebP · up to 5 MB. Your photo also appears in the sidebar.</p>
            {avatarFile && <span className="profile-avatar-file-meta">{avatarFile.name} · {(avatarFile.size / 1024 / 1024).toFixed(2)} MB · ready to upload</span>}
            <div className="profile-settings-avatar-actions">
              <button type="button" className="ghost" onClick={() => avatarInputRef.current?.click()} disabled={saving}>CHOOSE PHOTO</button>
              {(profile.avatar_url || avatarFile) && <button type="button" className="profile-remove-avatar" onClick={() => { setAvatarFile(null); setRemoveAvatar(true); setAvatarError(""); setSaved(false); }} disabled={saving}>REMOVE PHOTO</button>}
              <input ref={avatarInputRef} className="profile-avatar-input" type="file" accept="image/jpeg,image/png,image/webp" onChange={chooseAvatar} />
            </div>
            {avatarError && <p className="profile-avatar-error" role="alert">{avatarError}</p>}
          </div>
        </div>
        <div className="profile-settings-grid">
          <label>FULL NAME<input autoComplete="name" value={profile.full_name || ""} onChange={e => { setProfile({...profile, full_name:e.target.value}); setSaved(false); }} maxLength="200" placeholder="Your display name" /></label>
          <label>EMAIL ADDRESS<input type="email" autoComplete="email" value={profile.email || ""} onChange={e => { setProfile({...profile, email:e.target.value}); setSaved(false); }} maxLength="254" placeholder="you@example.com" /><small>Changing your email requires verification again.</small></label>
          <label>PROFESSIONAL TITLE<input value={profile.job_title || ""} onChange={e => { setProfile({...profile, job_title:e.target.value}); setSaved(false); }} maxLength="120" placeholder="e.g. Full-stack Developer" /></label>
          <label>COUNTRY<input autoComplete="country-name" value={profile.country || ""} onChange={e => { setProfile({...profile, country:e.target.value}); setSaved(false); }} maxLength="100" placeholder="Country" /></label>
          <label>CITY / REGION<input autoComplete="address-level2" value={profile.city || ""} onChange={e => { setProfile({...profile, city:e.target.value}); setSaved(false); }} maxLength="100" placeholder="City or region" /></label>
          <label>TIME ZONE<select value={profile.timezone || ""} onChange={e => { setProfile({...profile, timezone:e.target.value}); setSaved(false); }}>
            <option value="">Select a time zone</option><option value="Asia/Kabul">Asia/Kabul (UTC+04:30)</option><option value="Asia/Dubai">Asia/Dubai (UTC+04:00)</option><option value="Asia/Karachi">Asia/Karachi (UTC+05:00)</option><option value="Asia/Kolkata">Asia/Kolkata (UTC+05:30)</option><option value="Europe/London">Europe/London</option><option value="Europe/Berlin">Europe/Berlin</option><option value="America/New_York">America/New_York</option><option value="America/Los_Angeles">America/Los_Angeles</option><option value="UTC">UTC</option>
          </select></label>
          <label className="profile-field-wide">SKILLS / TECH STACK<input value={profile.skills || ""} onChange={e => { setProfile({...profile, skills:e.target.value}); setSaved(false); }} maxLength="500" placeholder="Python, Django, React, PostgreSQL" /><small>Use commas between skills and technologies. <span className="profile-character-count">{(profile.skills || "").length}/500</span></small></label>
          <label className="profile-field-wide">BIO<textarea value={profile.bio || ""} onChange={e => { setProfile({...profile, bio:e.target.value}); setSaved(false); }} maxLength="5000" placeholder="What are you building and what do you enjoy solving?" /><small className="profile-character-count">{(profile.bio || "").length}/5000 characters</small></label>
          <label>GITHUB PROFILE<input type="url" value={profile.github || ""} onChange={e => { setProfile({...profile, github:e.target.value}); setSaved(false); }} placeholder="https://github.com/username" /></label>
          <label>LINKEDIN PROFILE<input type="url" value={profile.linkedin || ""} onChange={e => { setProfile({...profile, linkedin:e.target.value}); setSaved(false); }} placeholder="https://linkedin.com/in/username" /></label>
          <label>X / SOCIAL PROFILE<input type="url" value={profile.x || ""} onChange={e => { setProfile({...profile, x:e.target.value}); setSaved(false); }} placeholder="https://x.com/username" /></label>
          <label>PERSONAL WEBSITE<input type="url" value={profile.website || ""} onChange={e => { setProfile({...profile, website:e.target.value}); setSaved(false); }} placeholder="https://example.com" /></label>
        </div>
        {saveError && <div className="error" role="alert">{saveError}</div>}
        <button className="primary" type="button" onClick={save} disabled={saving}>{saving ? "SAVING PROFILE…" : saved ? "SAVED ✓" : "SAVE CHANGES"}</button>
      </section>
    </div>
  );
}

function Settings({ go }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");
  const sections = [
    { id: "profile", category: "Account", icon: "◎", eyebrow: "IDENTITY", title: "Developer profile", description: "Manage your public identity, email, location, skills, avatar, and professional links.", action: "Open profile", route: "/profile", tags: "email country timezone avatar identity personal" },
    { id: "billing", category: "Account", icon: "◇", eyebrow: "PLAN & USAGE", title: "Billing and plans", description: "Review your subscription tier, plan details, and available usage information.", action: "Manage plan", route: "/billing", tags: "subscription payment pro free team enterprise" },
    { id: "workspace", category: "Workspace", icon: "⌘", eyebrow: "DEVELOPMENT", title: "Web IDE", description: "Open your coding workspace, editor preferences, terminal, and development tools.", action: "Open workspace", route: "/ide", tags: "editor code terminal runtime preferences" },
    { id: "team", category: "Workspace", icon: "⧉", eyebrow: "COLLABORATION", title: "Team and access", description: "Open team collaboration and workspace membership controls.", action: "Manage team", route: "/team", tags: "members invite roles collaboration permissions" },
    { id: "ai", category: "Workspace", icon: "✦", eyebrow: "INTELLIGENCE", title: "AI workspace", description: "Open the AI workspace and available assistant capabilities.", action: "Open AI", route: "/ai", tags: "assistant ai provider prompt engineering" },
    { id: "audit", category: "Security", icon: "◈", eyebrow: "SECURITY & ACTIVITY", title: "Audit log", description: "Review available account and workspace activity records.", action: "View activity", route: "/audit", tags: "security events history governance audit" },
    { id: "referrals", category: "Growth", icon: "↗", eyebrow: "COMMUNITY", title: "Referrals and rewards", description: "Manage your referral link and review the available referral program.", action: "Open referrals", route: "/referrals", tags: "invite reward referral growth pro" },
  ];
  const categories = ["All", "Account", "Workspace", "Security", "Growth"];
  const filtered = sections.filter(item => {
    const matchesCategory = category === "All" || item.category === category;
    const haystack = [item.title, item.description, item.eyebrow, item.tags, item.category].join(" ").toLowerCase();
    return matchesCategory && haystack.includes(query.trim().toLowerCase());
  });

  return (
    <div className="page settings-hub settings-center">
      <div className="settings-center-hero">
        <div className="settings-center-hero-copy">
          <div className="eyebrow">CONTROL PLANE / PREFERENCES</div>
          <h1>Settings Center</h1>
          <p>Your control room for account identity, development workspace, collaboration, and platform governance.</p>
          <div className="settings-hero-meta"><span><i className="settings-status-dot" /> PERSONAL WORKSPACE</span><span>DEVELOPER OS · 4.0</span></div>
        </div>
        <div className="settings-hero-orbit" aria-hidden="true"><div className="settings-orbit-ring settings-orbit-ring-one" /><div className="settings-orbit-ring settings-orbit-ring-two" /><div className="settings-orbit-core">D<span>OS</span></div><i className="settings-orbit-node settings-orbit-node-one" /><i className="settings-orbit-node settings-orbit-node-two" /><i className="settings-orbit-node settings-orbit-node-three" /></div>
      </div>

      <section className="settings-search-panel" aria-label="Find a setting">
        <div className="settings-search-icon" aria-hidden="true">⌕</div>
        <label className="settings-search-label" htmlFor="settings-center-search">Find a setting</label>
        <input id="settings-center-search" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search profile, billing, security, workspace…" autoComplete="off" />
        {query && <button type="button" className="settings-clear-search" onClick={() => setQuery("")} aria-label="Clear settings search">×</button>}
        <kbd>SEARCH</kbd>
      </section>

      <div className="settings-center-heading"><div><span className="panel-kicker">CONFIGURATION MAP</span><h2>Platform controls</h2><p>Choose a control area to manage the corresponding part of Developer OS.</p></div><span className="settings-result-count">{filtered.length} {filtered.length === 1 ? "CONTROL" : "CONTROLS"}</span></div>
      <div className="settings-category-tabs" role="group" aria-label="Filter settings by category">
        {categories.map(item => <button key={item} type="button" className={category === item ? "active" : ""} aria-pressed={category === item} onClick={() => setCategory(item)}>{item}</button>)}
      </div>

      {filtered.length ? <div className="settings-center-grid">
        {filtered.map((item, index) => <button key={item.id} type="button" className="settings-control-card" onClick={() => go(item.route)} style={{ "--settings-card-index": index }}>
          <span className="settings-control-card-top"><span className="settings-control-icon" aria-hidden="true">{item.icon}</span><span className="settings-control-category">{item.category}</span></span>
          <span className="panel-kicker">{item.eyebrow}</span>
          <strong className="settings-control-title">{item.title}</strong>
          <span className="settings-control-description">{item.description}</span>
          <span className="settings-control-action">{item.action}<span aria-hidden="true">↗</span></span>
        </button>)}
      </div> : <div className="settings-empty-state"><span aria-hidden="true">⌕</span><h3>No matching controls</h3><p>Try another search term or switch to a different category.</p><button type="button" className="ghost" onClick={() => { setQuery(""); setCategory("All"); }}>Clear filters</button></div>}

      <section className="settings-bottom-grid">
        <div className="settings-info-panel"><span className="settings-info-icon">⌘</span><div><span className="panel-kicker">QUICK NAVIGATION</span><h3>Move faster</h3><p>Use the global search shortcut to jump to tools and pages without leaving your current workflow.</p><div className="settings-shortcut"><kbd>CTRL</kbd><span>+</span><kbd>K</kbd><span>Global search</span></div></div></div>
        <div className="settings-info-panel settings-info-panel-accent"><span className="settings-info-icon">⛨</span><div><span className="panel-kicker">ACCOUNT SAFETY</span><h3>Security and governance</h3><p>Review activity records and use account controls to keep your workspace organized.</p><button type="button" className="settings-inline-action" onClick={() => go("/audit")}>Open audit log <span aria-hidden="true">→</span></button></div></div>
      </section>
      <p className="settings-center-footnote">Settings are grouped by control area. Changes made on linked pages follow each page’s own save and permission rules.</p>
    </div>
  );
}

export default function App() {
  const [authenticated,setAuthenticated]=useState(Boolean(getAccessToken()));
  const [user,setUser]=useState(null);
  const [path,go]=useRoute();

  useEffect(()=>{if(authenticated)apiFetch("/profile/").then(r=>r.json()).then(p=>setUser(p.user||p)).catch(()=>setAuthenticated(false));},[authenticated]);
  useEffect(()=>{
    if(!authenticated) return;
    const referralCode = new URLSearchParams(window.location.search).get("ref");
    if(!referralCode) return;
    apiFetch("/referrals/",{method:"POST",body:JSON.stringify({code:referralCode})}).catch(()=>{}).finally(()=>{
      const url = new URL(window.location.href);
      url.searchParams.delete("ref");
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    });
  },[authenticated]);
  useEffect(()=>{const f=()=>setAuthenticated(false);window.addEventListener("auth:expired",f);const k=e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();document.querySelector(".global-search input")?.focus();}};window.addEventListener("keydown",k);return()=>{window.removeEventListener("auth:expired",f);window.removeEventListener("keydown",k)};},[]);

  if(!authenticated)return <Auth onReady={()=>setAuthenticated(true)}/>;
  if(!user)return <div className="boot">INITIALIZING DEVELOPER OS <span>██████████</span></div>;

  const projectMatch=path.match(/^\/projects\/(\d+)/);
  let current=path==="/"?"dashboard":path.split("/")[1]||"dashboard";
  let content;
  if(projectMatch) content=<ProjectDetail id={projectMatch[1]} go={go}/>;
  else if(current==="dashboard") content=<Dashboard go={go}/>;
  else if(current==="explore") content=<Explore/>;
  else if(current==="projects") content=<Projects go={go}/>;
  else if(current==="ide") content=<ProIDE go={go}/>;
  else if(current==="ai") content=<AI/>;
  else if(current==="search") content=<SearchPage/>;
  else if(current==="team") content=<Team/>;
  else if(current==="referrals") content=<Referral go={go}/>;
  else if(current==="billing") content=<Billing/>;
  else if(current==="profile") content=<Profile onProfileUpdate={setUser}/>;
  else if(current==="settings") content=<Settings go={go}/>;
  else if(current==="audit") content=<Audit/>;
  else content=<Dashboard go={go}/>;

  return <><FullscreenExperience /><Shell user={user} onLogout={()=>setAuthenticated(false)} go={go} current={current}>{content}</Shell></>;
}