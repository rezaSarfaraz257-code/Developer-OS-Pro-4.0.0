import test from "node:test";
import assert from "node:assert/strict";
import {
  WORKSPACE_RECOVERY_VERSION,
  MAX_RECOVERY_ENTRIES,
  parseWorkspaceRecoveryJournal,
  getWorkspaceRecoverySnapshots,
  upsertWorkspaceRecoverySnapshot,
  removeWorkspaceRecoverySnapshot,
} from "../src/ide/workspaceRecovery.js";

const snapshot = (overrides = {}) => ({
  workspace_id: "7",
  path: "src/main.js",
  content: "local edit",
  base_content: "server base",
  base_revision: 12,
  updated_at: 100,
  ...overrides,
});

test("recovery journal rejects corrupt, unknown and malformed storage safely", () => {
  assert.deepEqual(parseWorkspaceRecoveryJournal("broken"), { version: WORKSPACE_RECOVERY_VERSION, entries: [] });
  assert.deepEqual(parseWorkspaceRecoveryJournal(JSON.stringify({ version: 99, entries: [snapshot()] })), { version: WORKSPACE_RECOVERY_VERSION, entries: [] });
  const parsed = parseWorkspaceRecoveryJournal(JSON.stringify({
    version: WORKSPACE_RECOVERY_VERSION,
    entries: [null, snapshot(), snapshot({ content: 7 }), snapshot({ workspace_id: " " })],
  }));
  assert.deepEqual(parsed.entries, [snapshot()]);
});

test("recovery snapshots are isolated by workspace and ordered oldest first", () => {
  const journal = { version: WORKSPACE_RECOVERY_VERSION, entries: [
    snapshot({ path: "b.js", updated_at: 200 }),
    snapshot({ path: "a.js", updated_at: 100 }),
    snapshot({ workspace_id: "8", path: "a.js", updated_at: 50 }),
  ] };
  assert.deepEqual(getWorkspaceRecoverySnapshots(journal, 7).map((item) => item.path), ["a.js", "b.js"]);
  assert.deepEqual(getWorkspaceRecoverySnapshots(journal, null), []);
});

test("upsert replaces only the same workspace and path without mutating input", () => {
  const original = { version: WORKSPACE_RECOVERY_VERSION, entries: [
    snapshot({ content: "old" }),
    snapshot({ workspace_id: "8", content: "other workspace" }),
  ] };
  const next = upsertWorkspaceRecoverySnapshot(original, snapshot({ content: "new", updated_at: 300 }));
  assert.deepEqual(next.entries, [
    original.entries[1],
    snapshot({ content: "new", updated_at: 300 }),
  ]);
  assert.equal(original.entries[0].content, "old");
});

test("invalid or unscoped snapshots cannot enter the recovery journal", () => {
  const empty = { version: WORKSPACE_RECOVERY_VERSION, entries: [] };
  assert.deepEqual(upsertWorkspaceRecoverySnapshot(empty, snapshot({ workspace_id: "" })), empty);
  assert.deepEqual(upsertWorkspaceRecoverySnapshot(empty, snapshot({ base_revision: null })), empty);
  assert.deepEqual(upsertWorkspaceRecoverySnapshot(empty, snapshot({ content: "x".repeat(750_001) })), empty);
});

test("recovery journal evicts oldest entries and stays within its entry bound", () => {
  const entries = Array.from({ length: MAX_RECOVERY_ENTRIES }, (_, index) =>
    snapshot({ path: `file-${index}.js`, updated_at: index })
  );
  const next = upsertWorkspaceRecoverySnapshot({ version: WORKSPACE_RECOVERY_VERSION, entries },
    snapshot({ path: "new.js", updated_at: 10_000 }));
  assert.equal(next.entries.length, MAX_RECOVERY_ENTRIES);
  assert.equal(next.entries.some((item) => item.path === "file-0.js"), false);
  assert.equal(next.entries.at(-1).path, "new.js");
});

test("removal is scoped to the exact workspace and path", () => {
  const journal = { version: WORKSPACE_RECOVERY_VERSION, entries: [
    snapshot(),
    snapshot({ workspace_id: "8" }),
    snapshot({ path: "other.js" }),
  ] };
  const next = removeWorkspaceRecoverySnapshot(journal, 7, "src/main.js");
  assert.deepEqual(next.entries, [journal.entries[1], journal.entries[2]]);
  assert.equal(journal.entries.length, 3);
});
