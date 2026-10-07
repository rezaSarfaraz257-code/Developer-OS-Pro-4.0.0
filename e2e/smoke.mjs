#!/usr/bin/env node
const base = (process.argv[2] || "http://127.0.0.1").replace(/\/$/, "");
const api = `${base}/api`;

async function request(path, options = {}) {
  const response = await fetch(`${api}${path}`, {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
  });
  let body = {};
  try { body = await response.json(); } catch {}
  if (!response.ok) {
    throw new Error(`${options.method || "GET"} ${path} -> ${response.status}: ${JSON.stringify(body)}`);
  }
  return body;
}

const suffix = `${Date.now()}${Math.floor(Math.random() * 10000)}`;
const username = `e2e_${suffix}`;
const email = `${username}@example.test`;
const password = "E2E-long-password-12345!";

await request("/health/");
await request("/health/ready/");

await request("/register/", {
  method: "POST",
  body: JSON.stringify({username, email, password, first_name: "E2E", last_name: "User"}),
});

const token = await request("/token/", {
  method: "POST",
  body: JSON.stringify({username, password}),
});
if (!token.access) throw new Error("Login did not return an access token.");

const auth = {"Authorization": `Bearer ${token.access}`};
const project = await request("/projects/", {
  method: "POST",
  headers: auth,
  body: JSON.stringify({title: "E2E Project", description: "validation", category: "General", status: "Planning", priority: "medium", tags: [], stack: []}),
});
if (!project.id) throw new Error("Project creation failed.");

const task = await request("/tasks/", {
  method: "POST",
  headers: auth,
  body: JSON.stringify({project: project.id, title: "E2E task", status: "todo", priority: "high", tags: []}),
});
if (!task.id) throw new Error("Task creation failed.");

const capabilities = await request("/ide/capabilities/", {headers: auth});
if (!capabilities.runner || !capabilities.runner.runtimes || !capabilities.plan || !capabilities.limits) throw new Error("IDE capability/entitlement contract failed.");
if (!["free","pro","team","enterprise","admin"].includes(capabilities.plan)) throw new Error("Unknown IDE entitlement plan.");

const workspace = await request("/ide/workspaces/", {
  method: "POST",
  headers: auth,
  body: JSON.stringify({name: "E2E Workspace", files: {"main.py": "print('hello')"}, active_file: "main.py"}),
});
if (!workspace.id) throw new Error("Workspace creation failed.");

let fileRevision = workspace.revision;
const createdFile = await request(`/ide/workspaces/${workspace.id}/files/`, {
  method: "POST", headers: auth,
  body: JSON.stringify({action: "create", path: "src/e2e.txt", content: "hello", revision: fileRevision}),
});
if (!createdFile.files?.["src/e2e.txt"]) throw new Error("File create E2E failed.");
fileRevision = createdFile.revision;

const writtenFile = await request(`/ide/workspaces/${workspace.id}/files/`, {
  method: "POST", headers: auth,
  body: JSON.stringify({action: "write", path: "src/e2e.txt", content: "hello updated", revision: fileRevision}),
});
if (writtenFile.files?.["src/e2e.txt"] !== "hello updated") throw new Error("File write E2E failed.");
fileRevision = writtenFile.revision;

const renamedFile = await request(`/ide/workspaces/${workspace.id}/files/`, {
  method: "POST", headers: auth,
  body: JSON.stringify({action: "rename", path: "src/e2e.txt", to: "src/e2e-renamed.txt", revision: fileRevision}),
});
if (!renamedFile.files?.["src/e2e-renamed.txt"] || renamedFile.files?.["src/e2e.txt"]) throw new Error("File rename E2E failed.");


const ai = await request("/ai/actions/", {
  method: "POST",
  headers: auth,
  body: JSON.stringify({action: "plan", input: "Ship the E2E validation project safely.", project: project.id}),
});
if (!ai.answer) throw new Error("AI action returned no answer.");


// --- P0 IDE execution / process lifecycle / debugger contract ---
const execute = await request(`/ide/workspaces/${workspace.id}/execute/`, {
  method: "POST",
  headers: auth,
  body: JSON.stringify({
    command: "python main.py",
    active_file: "main.py",
  }),
});
if (execute.command !== "python main.py" || execute.active_file !== "main.py" || execute.status !== "success" || execute.exit_code !== 0 || String(execute.stdout || "").trim() !== "hello") {
  throw new Error("IDE execute E2E failed.");
}

const processStarted = await request(`/ide/workspaces/${workspace.id}/process/start/`, {
  method: "POST",
  headers: auth,
  body: JSON.stringify({command: "python -c \"import time; print('PROCESS_READY', flush=True); time.sleep(30)\""}),
});
if (!processStarted.id) throw new Error("IDE process start failed.");

const processes = await request(`/ide/workspaces/${workspace.id}/processes/`, {headers: auth});
const processList = Array.isArray(processes) ? processes : processes.processes;
if (!Array.isArray(processList) || !processList.some((p) => String(p.id) === String(processStarted.id))) {
  throw new Error("Started process was not visible in process list.");
}

const stopped = await request(`/ide/workspaces/${workspace.id}/process/${encodeURIComponent(processStarted.id)}/stop/`, {
  method: "POST",
  headers: auth,
  body: JSON.stringify({}),
});
if (stopped.status === "running") throw new Error("Process stop did not terminate the process.");

const debugStarted = await request(`/ide/workspaces/${workspace.id}/debug/`, {
  method: "POST",
  headers: auth,
  body: JSON.stringify({action: "start", path: "main.py", line: 1}),
});
if (!debugStarted.session_id) throw new Error("Debugger session did not start.");

const debugBreakpoint = await request(`/ide/workspaces/${workspace.id}/debug/`, {
  method: "POST",
  headers: auth,
  body: JSON.stringify({
    action: "set_breakpoint",
    session_id: debugStarted.session_id,
    path: "main.py",
    line: 1,
  }),
});
if (!Array.isArray(debugBreakpoint.breakpoints) || !debugBreakpoint.breakpoints.length) {
  throw new Error("Debugger breakpoint contract failed.");
}

const debugStopped = await request(`/ide/workspaces/${workspace.id}/debug/`, {
  method: "POST",
  headers: auth,
  body: JSON.stringify({action: "stop", session_id: debugStarted.session_id}),
});
if (debugStopped.state !== "stopped") throw new Error("Debugger stop contract failed.");

const usage = await request("/usage/", {headers: auth});
if (!usage.metrics || usage.plan !== "free") throw new Error("Usage endpoint failed.");

const sessions = await request("/auth/sessions/", {headers: auth});
if (!Array.isArray(sessions)) throw new Error("Session management failed.");
const mfa = await request("/auth/mfa/setup/", {method:"POST", headers: auth});
if (!mfa.secret || !mfa.otpauth_uri) throw new Error("MFA setup failed.");
await request("/analytics/events/", {method:"POST", headers: auth, body: JSON.stringify({name:"smoke_event", properties:{source:"ci"}})});
const ticket = await request("/support/tickets/", {method:"POST", headers: auth, body: JSON.stringify({subject:"Smoke support ticket", body:"Automated production validation", priority:"low"})});
if (!ticket.id) throw new Error("Support workflow failed.");
const exportJob = await request("/account/export/", {method:"POST", headers: auth});
if (!exportJob.id) throw new Error("Data export workflow failed.");

console.log("E2E smoke PASS", JSON.stringify({
  user: username,
  project: project.id,
  task: task.id,
  workspace: workspace.id,
  ai_mode: ai.mode,
}));
