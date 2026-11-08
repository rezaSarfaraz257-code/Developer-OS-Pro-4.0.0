import test from "node:test";
import assert from "node:assert/strict";
import {
  normalizeWorkspaceRecoverySnapshot,
  saveWorkspaceRecoverySnapshot,
  saveWorkspaceRecoverySnapshotFallbackSync,
  loadWorkspaceRecoverySnapshot,
  deleteWorkspaceRecoverySnapshot,
} from "../src/ide/workspaceRecovery.js";

function installLocalStorage() {
  // Keep fallback tests deterministic across Node versions that expose IndexedDB.
  globalThis.indexedDB = undefined;
  const values = new Map();
  globalThis.localStorage = {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
  return values;
}

const validSnapshot = (overrides = {}) => ({
  workspace_id: "42",
  base_revision: 7,
  active_file: "src/main.js",
  updated_at: 100,
  files: { "src/main.js": "console.log('recovered');" },
  ...overrides,
});

test("normalizes valid recovery snapshots without mutating their source", () => {
  const input = validSnapshot({ workspace_id: 42, base_revision: "7" });
  const normalized = normalizeWorkspaceRecoverySnapshot(input);
  assert.deepEqual(normalized, {
    schema_version: 1,
    workspace_id: "42",
    base_revision: 7,
    active_file: "src/main.js",
    updated_at: 100,
    files: { "src/main.js": "console.log('recovered');" },
  });
  assert.equal(input.workspace_id, 42);
});

test("rejects malformed, unsafe, and oversized recovery data", () => {
  assert.equal(normalizeWorkspaceRecoverySnapshot(null), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ base_revision: -1 })), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ base_revision: true })), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ files: [] })), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ files: { "../secrets": "x" } })), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ files: { "/etc/passwd": "x" } })), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ files: { ".git/config": "x" } })), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ files: { "bad.js": "x\0y" } })), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ files: { "large.js": "x".repeat(1_000_001) } })), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ workspace_id: " " })), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ workspace_id: "x".repeat(129) })), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ files: { "..\\secrets": "x" } })), null);
  assert.equal(normalizeWorkspaceRecoverySnapshot(validSnapshot({ files: JSON.parse('{"__proto__":"safe"}') })).files.__proto__, "safe");
});

test("synchronous page-shutdown fallback preserves a bounded dirty snapshot", () => {
  const storage = installLocalStorage();
  assert.equal(saveWorkspaceRecoverySnapshotFallbackSync(validSnapshot()), true);
  assert.equal(JSON.parse(storage.get("dos-workspace-recovery:42")).files["src/main.js"], "console.log('recovered');");
  const oversized = Object.fromEntries(Array.from({ length: 5 }, (_, index) => [`large-${index}.js`, "x".repeat(900_000)]));
  assert.equal(saveWorkspaceRecoverySnapshotFallbackSync(validSnapshot({ files: oversized })), false);
});

test("persists and restores a recovery snapshot through the bounded local fallback", async () => {
  const storage = installLocalStorage();
  const saved = await saveWorkspaceRecoverySnapshot(validSnapshot());
  assert.equal(saved.storage, "localstorage");
  const restored = await loadWorkspaceRecoverySnapshot("42");
  assert.equal(restored.base_revision, 7);
  assert.deepEqual(restored.files, { "src/main.js": "console.log('recovered');" });
  assert.equal(storage.size, 1);
});

test("ignores corrupt or cross-workspace fallback snapshots", async () => {
  const storage = installLocalStorage();
  storage.set("dos-workspace-recovery:42", JSON.stringify(validSnapshot({ workspace_id: "99" })));
  assert.equal(await loadWorkspaceRecoverySnapshot("42"), null);
  storage.set("dos-workspace-recovery:42", "{invalid");
  assert.equal(await loadWorkspaceRecoverySnapshot("42"), null);
});

test("fails closed when both durable storage and the bounded fallback are unavailable", async () => {
  globalThis.localStorage = {
    getItem: () => null,
    setItem: () => { throw new Error("quota exceeded"); },
    removeItem: () => {},
  };
  await assert.rejects(
    saveWorkspaceRecoverySnapshot(validSnapshot()),
    /Could not persist workspace recovery data/,
  );
});

test("deletes the workspace-specific recovery snapshot without touching other workspaces", async () => {
  const storage = installLocalStorage();
  storage.set("dos-workspace-recovery:42", JSON.stringify(validSnapshot()));
  storage.set("dos-workspace-recovery:43", JSON.stringify(validSnapshot({ workspace_id: "43" })));
  await deleteWorkspaceRecoverySnapshot("42");
  assert.equal(await loadWorkspaceRecoverySnapshot("42"), null);
  assert.notEqual(await loadWorkspaceRecoverySnapshot("43"), null);
});
