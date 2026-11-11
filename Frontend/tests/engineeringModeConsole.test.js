import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const testsDir = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(testsDir, "../src");
const read = (file) => fs.readFileSync(path.join(srcDir, file), "utf8");

test("Command Center exposes the unified engineering mode console", () => {
  const app = read("App.jsx");
  const consoleSource = read("EngineeringModeConsole.jsx");

  assert.match(app, /import EngineeringModeConsole from ["']\.\/EngineeringModeConsole\.jsx["']/);
  assert.match(app, /<EngineeringModeConsole go=\{go\} \/>/);
  for (const mode of ["Manual Build", "Agent Build", "Hybrid Build"]) {
    assert.ok(consoleSource.includes(mode), `missing build mode: ${mode}`);
  }
});

test("Agent console uses the owner-scoped durable planning lifecycle", () => {
  const source = read("EngineeringModeConsole.jsx");

  assert.match(source, /apiFetch\(["']\/ai\/agent\/runs\/["']/);
  assert.match(source, /\/ai\/agent\/runs\/\$\{id\}\//);
  assert.match(source, /dos:pending-agent-request/);
  assert.match(source, /mutationLockRef\.current/);
  assert.match(source, /runRequestSequenceRef\.current/);
  assert.match(source, /requestSequence !== runRequestSequenceRef\.current/);
  assert.match(source, /if \(!response\.ok \|\| !payload\?\.id\)/);
  assert.match(source, /disabled=\{busy \|\| loadingWorkspaces \|\| !workspaces\.length\}/);
  assert.match(source, /String\(task\.agent_role \|\| ["']engineering["']\)/);
  assert.match(source, /pendingRequestRef\.current = pendingRequest/);
  assert.match(source, /window\.sessionStorage\.removeItem\("dos:pending-agent-request"\)/);
  assert.match(source, /setWorkspaceId\(\(current\) => current \|\| String\(items\[0\]\.id\)\)/);
  assert.match(source, /\/ai\/agent\/runs\/\$\{run\.id\}\/approve\//);
  assert.match(source, /approvalError\.code = payload\?\.code/);
  assert.match(source, /requestError\?\.code === ["']stale_workspace["']/);
  assert.match(source, /\/ai\/agent\/runs\/\$\{run\.id\}\/cancel\//);
  assert.match(source, /ACTIVE_STATUSES\.has\(latest\.status\)/);
  assert.match(source, /latest \? 2200 : 3500/);
});

test("Agent approval is not presented as code execution or workspace mutation", () => {
  const source = read("EngineeringModeConsole.jsx");

  assert.match(source, /plan_only_no_workspace_mutations/);
  assert.match(source, /does not execute tasks or modify workspace files/);
  assert.match(source, /Task execution remains unavailable in this runtime/);
});

test("Hybrid handoff restores the selected workspace in the Web IDE", () => {
  const consoleSource = read("EngineeringModeConsole.jsx");
  const ide = read("ProIDE.jsx");

  assert.match(consoleSource, /dos:requested-ide-workspace/);
  assert.match(ide, /dos:requested-ide-workspace/);
  assert.match(ide, /load\(preferred\|\|workspaces\[0\]\)/);
});

test("engineering mode console styling is responsive and motion-safe", () => {
  const component = read("EngineeringModeConsole.jsx");
  const css = read("EngineeringModeConsole.css");

  assert.match(component, /import ["']\.\/EngineeringModeConsole\.css["']/);
  assert.match(css, /@media\(max-width:600px\)/);
  assert.match(css, /@media\(prefers-reduced-motion:reduce\)/);
  assert.match(css, /:focus-visible/);
});
