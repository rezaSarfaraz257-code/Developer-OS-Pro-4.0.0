import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  executionStorageKey,
  getAcceptedCriteria,
  getExecutionPollDelay,
  isSyncTerminal,
  isVerificationTerminal,
  normalizeExecutionId,
  normalizeSavedSyncState,
  normalizeSyncSnapshot,
  normalizeTaskExecution,
} from "../src/agentExecutionWorkflow.js";

const testsDir = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(testsDir, "../src");
const read = (file) => fs.readFileSync(path.join(srcDir, file), "utf8");

test("Agent execution identifiers reject path, boolean, fractional and unsafe values", () => {
  assert.equal(normalizeExecutionId(17), 17);
  assert.equal(normalizeExecutionId("17"), 17);
  for (const value of [true, false, 0, -1, 1.2, "task/17", "17?x=1", "9".repeat(30), null, {}]) {
    assert.equal(normalizeExecutionId(value), null);
  }
});

test("Runner sync normalization binds workspace and preserves a mismatched revision for fail-closed UI", () => {
  const sync = normalizeSyncSnapshot({
    job_id: 31,
    status: "succeeded",
    workspace_id: 7,
    revision: 13,
    coalesced: true,
  }, { workspaceId: 7, revision: 12 });
  assert.deepEqual(sync, {
    id: 31,
    status: "succeeded",
    workspaceId: 7,
    revision: 13,
    coalesced: true,
    error: "",
  });
  assert.equal(normalizeSyncSnapshot({ job_id: 31, status: "succeeded", workspace_id: 8, revision: 13 }, { workspaceId: 7 }), null);
  assert.equal(normalizeSyncSnapshot({ job_id: "../31", status: "queued" }), null);
  assert.equal(normalizeSyncSnapshot({ job_id: 31, status: "succeeded", workspace_id: 7 }, { workspaceId: 7, revision: 12 }).revision, null);
  assert.equal(normalizeSyncSnapshot({ job_id: 31, status: "succeeded", revision: 12 }, { workspaceId: 7 }), null);
  assert.equal(normalizeSavedSyncState({
    runId: 4, taskId: 9, sync: { id: 31, status: "queued", revision: 12 },
  }, { runId: 4, taskId: 9, workspaceId: 7 }), null);
});

test("saved sync recovery is scoped to the exact run, task and workspace and never stores approval credentials", () => {
  const saved = {
    runId: 4,
    taskId: 9,
    sync: { id: 31, status: "queued", workspaceId: 7, revision: 12 },
  };
  assert.deepEqual(normalizeSavedSyncState(saved, { runId: 4, taskId: 9, workspaceId: 7 }), {
    id: 31, status: "queued", workspaceId: 7, revision: 12, coalesced: false, error: "",
  });
  assert.equal(normalizeSavedSyncState(saved, { runId: 4, taskId: 9, workspaceId: 8 }), null);
  assert.equal(normalizeSavedSyncState(saved, { runId: 5, taskId: 9, workspaceId: 7 }), null);
  assert.equal(executionStorageKey(4, 9), "dos:agent-sync:4:9");
  assert.equal(executionStorageKey("../4", 9), "");
});

test("task execution normalization verifies run/task identity and keeps bounded evidence", () => {
  const payload = {
    run_id: 4,
    task_id: 9,
    status: "awaiting_approval",
    acceptance_criteria: ["Build succeeds", "Tests pass", "", 12],
    revision: 12,
    attempts: 1,
    evidence: { state: "passed", exit_code: 0, output_persisted: false, diagnostic_signals: [null, { code: "python_test_failure", source: "stderr" }], history: [{ state: "failed", runner_status: "failed", revision: 11, exit_code: 1, iteration: 1 }, null] },
    error: "",
    job: { id: 31, status: "succeeded", attempts: 1, max_attempts: 1 },
  };
  const normalized = normalizeTaskExecution(payload, { runId: 4, taskId: 9 });
  assert.equal(normalized.status, "awaiting_approval");
  assert.deepEqual(normalized.acceptanceCriteria, ["Build succeeds", "Tests pass"]);
  assert.equal(normalized.job.id, 31);
  assert.equal(normalized.evidence.output_persisted, false);
  assert.equal(normalized.evidence.history.length, 1);
  assert.equal(normalized.evidence.history[0].revision, 11);
  assert.deepEqual(normalized.evidence.diagnostic_signals, [{ code: "python_test_failure", source: "stderr" }]);
  assert.equal(normalizeTaskExecution(payload, { runId: 5, taskId: 9 }), null);
  assert.equal(normalizeTaskExecution(payload, { runId: 4, taskId: 10 }), null);
  assert.equal(normalizeTaskExecution([], { runId: 4, taskId: 9 }), null);

  const acceptedPayload = {
    run_id: 4,
    task_id: 9,
    status: "succeeded",
    acceptance_criteria: ["Build succeeds"],
    evidence: { human_acceptance: {
      user_id: 12,
      accepted_at: "2026-11-27T00:00:00+00:00",
      accepted_criteria: ["Build succeeds"],
      criteria_scope: "agent_task",
    } },
  };
  assert.equal(normalizeTaskExecution(acceptedPayload).status, "succeeded");
  for (const human_acceptance of [
    { ...acceptedPayload.evidence.human_acceptance, criteria_scope: "run" },
    { ...acceptedPayload.evidence.human_acceptance, accepted_criteria: [] },
    { ...acceptedPayload.evidence.human_acceptance, accepted_at: "not-a-date" },
    { ...acceptedPayload.evidence.human_acceptance, accepted_at: "2026-11-27T00:00:00" },
    { ...acceptedPayload.evidence.human_acceptance, user_id: null },
  ]) {
    assert.equal(
      normalizeTaskExecution({ ...acceptedPayload, evidence: { human_acceptance } }).status,
      "unknown",
    );
  }
  assert.equal(normalizeTaskExecution({
    ...acceptedPayload,
    evidence: {},
  }).status, "unknown");
});

test("polling is bounded and verification terminal states stop repeated polling", () => {
  assert.equal(getExecutionPollDelay(0), 2200);
  assert.equal(getExecutionPollDelay(1), 4400);
  assert.equal(getExecutionPollDelay(99), 12000);
  assert.equal(getExecutionPollDelay(0, true), 15000);
  assert.equal(isSyncTerminal("succeeded"), true);
  assert.equal(isSyncTerminal("stale"), true);
  assert.equal(isSyncTerminal("running"), false);
  assert.equal(isVerificationTerminal("awaiting_approval", "succeeded"), true);
  assert.equal(isVerificationTerminal("running", "queued"), false);
});

test("acceptance payload preserves the exact server criterion order", () => {
  const criteria = ["Build passes", "Tests pass", "No regression"];
  assert.deepEqual(getAcceptedCriteria(criteria, ["No regression", "Build passes", "Tests pass"]), criteria);
  assert.deepEqual(getAcceptedCriteria(criteria, ["Build passes"]), ["Build passes"]);
  assert.deepEqual(getAcceptedCriteria("invalid", criteria), []);
});

test("Agent task console connects proposal, approval, Runner sync, verification and acceptance APIs", () => {
  const source = read("AgentTaskExecutionWorkflow.jsx");
  const consoleSource = read("EngineeringModeConsole.jsx");
  for (const endpoint of [
    "/ai/agent/apply/",
    "/ai/agent/sync-jobs/",
    "/verify/",
    "/verification/",
    "/accept/",
  ]) assert.ok(source.includes(endpoint), "missing workflow endpoint: " + endpoint);
  assert.ok(source.includes('kind === "repair" ? "repair-proposal" : "proposal"'));

  for (const contract of [
    "payload.workspace_mutated !== false",
    "payload.proposal.requires_approval !== true",
    "!reviewConfirmed",
    "approval_token: proposal.approvalToken",
    "accepted_criteria: getAcceptedCriteria",
    "normalized.status !== \"succeeded\" || !normalized.evidence?.human_acceptance",
    "VERIFICATION_PROFILES = [",
    "Commands are selected from a server-side allowlist",
    `(!syncJob || syncJob.status === "stale")`,
    `(run?.status !== "failed" || effectiveStatus === "failed")`,
  ]) assert.ok(source.includes(contract), "missing execution safeguard: " + contract);

  assert.ok(!source.includes("sessionStorage.setItem(key, proposal.approvalToken)"));
  assert.ok(!source.includes("body: JSON.stringify({ command:"));
  assert.ok(!source.includes("stdout") && !source.includes("stderr"));
  assert.ok(consoleSource.includes('<AgentTaskExecutionWorkflow run={run} onRefreshRun={refreshRun} />'));
  assert.ok(!consoleSource.includes("Task execution remains unavailable in this runtime"));
});

test("execution workflow styling supports mobile layouts and keyboard focus", () => {
  const css = read("EngineeringModeConsole.css");
  assert.match(css, /\.cc-agent-execution/);
  assert.match(css, /@media\(max-width:600px\)/);
  assert.match(css, /@media\(prefers-reduced-motion:reduce\)/);
  assert.match(css, /\.cc-agent-execution :focus-visible/);
  assert.match(css, /\.cc-agent-proposal-files pre/);
});
