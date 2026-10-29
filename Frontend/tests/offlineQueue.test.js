import test from "node:test";
import assert from "node:assert/strict";
import {
  parseOfflineQueue,
  queueWorkspacePatch,
  getWorkspacePatches,
  removeWorkspacePatches,
  canSafelySyncOfflinePatches,
  removeSyncedWorkspacePatches,
  rebaseWorkspacePatches,
} from "../src/ide/offlineQueue.js";

test("offline queue parsing tolerates corrupt storage and preserves only file patches", () => {
  assert.deepEqual(parseOfflineQueue("not-json"), []);
  assert.deepEqual(parseOfflineQueue('{"path":"src/main.js","content":"ok"}'), []);
  assert.deepEqual(parseOfflineQueue(JSON.stringify([
    null,
    1,
    { path: "src/main.js", content: "safe" },
    { path: "src/other.js", content: 3 },
    { path: 7, content: "invalid" },
  ])), [{ path: "src/main.js", content: "safe" }]);
});

test("queueing a patch replaces only the same workspace and path", () => {
  const original = [
    { workspace_id: 1, path: "src/main.js", content: "old" },
    { workspace_id: 2, path: "src/main.js", content: "other workspace" },
    { workspace_id: 1, path: "src/app.js", content: "keep" },
  ];
  const next = queueWorkspacePatch(original, {
    workspace_id: "1",
    path: "src/main.js",
    content: "new",
    base_revision: 8,
  });

  assert.deepEqual(next, [
    original[1],
    original[2],
    { workspace_id: "1", path: "src/main.js", content: "new", base_revision: 8 },
  ]);
  assert.equal(original[0].content, "old", "queue updates must not mutate prior state");
});

test("invalid patches cannot enter the offline queue", () => {
  const original = [{ workspace_id: 3, path: "a.js", content: "keep" }];
  assert.strictEqual(queueWorkspacePatch(original, { workspace_id: 3, path: "b.js", content: null }), original);
  assert.strictEqual(queueWorkspacePatch(original, { workspace_id: "", path: "b.js", content: "x" }), original);
  assert.strictEqual(queueWorkspacePatch(original, { workspace_id: "   ", path: "b.js", content: "x" }), original);
});

test("workspace filtering and removal do not cross workspace boundaries or discard legacy entries", () => {
  const queue = [
    { workspace_id: 1, path: "a.js", content: "one" },
    { workspace_id: "2", path: "a.js", content: "two" },
    { path: "legacy.js", content: "legacy" },
  ];
  assert.deepEqual(getWorkspacePatches(queue, "1"), [queue[0]]);
  assert.deepEqual(getWorkspacePatches(queue, null), []);
  assert.deepEqual(removeWorkspacePatches(queue, 1), [queue[1], queue[2]]);
  assert.deepEqual(removeWorkspacePatches(queue, null), queue);
});

test("offline replay requires one explicit, safe, matching base revision", () => {
  const patches = [
    { workspace_id: 7, path: "a.js", content: "A", base_revision: 12 },
    { workspace_id: 7, path: "b.js", content: "B", base_revision: "12" },
  ];
  assert.equal(canSafelySyncOfflinePatches(patches, 12), true);
  assert.equal(canSafelySyncOfflinePatches(patches, "12"), true);
  assert.equal(canSafelySyncOfflinePatches(patches, 13), false);
  assert.equal(canSafelySyncOfflinePatches([{ ...patches[0], base_revision: 11 }, patches[1]], 12), false);
  assert.equal(canSafelySyncOfflinePatches([{ ...patches[0], base_revision: undefined }], 0), false);
  assert.equal(canSafelySyncOfflinePatches([{ ...patches[0], base_revision: Number.MAX_SAFE_INTEGER + 1 }], 0), false);
  assert.equal(canSafelySyncOfflinePatches([], 0), false);
  assert.equal(canSafelySyncOfflinePatches([
    { ...patches[0], workspace_id: 7 },
    { ...patches[1], workspace_id: 8 },
  ], 12), false, "one replay batch must never span workspaces");
  assert.equal(canSafelySyncOfflinePatches([
    { ...patches[0], workspace_id: 7 },
    { ...patches[1], workspace_id: "7" },
  ], 12), true, "numeric and string forms of the same workspace ID are equivalent");
  assert.equal(canSafelySyncOfflinePatches([
    patches[0],
    { ...patches[1], path: patches[0].path },
  ], 12), false, "duplicate paths make replay order ambiguous");
  assert.equal(canSafelySyncOfflinePatches([
    { ...patches[0], workspace_id: "" },
  ], 12), false, "unscoped edits cannot be replayed into a workspace");
  assert.equal(canSafelySyncOfflinePatches([
    { ...patches[0], workspace_id: "   " },
  ], 12), false, "whitespace-only workspace IDs cannot be replayed");
});

test("successful replay removes only exact saved versions and preserves newer queued edits", () => {
  const queued = [
    { workspace_id: 4, path: "a.js", content: "saved", base_revision: 3 },
    { workspace_id: 4, path: "a.js", content: "newer edit", base_revision: 4 },
    { workspace_id: 9, path: "a.js", content: "other workspace", base_revision: 3 },
    { path: "legacy.js", content: "legacy" },
  ];
  const remaining = removeSyncedWorkspacePatches(queued, [queued[0]]);
  assert.deepEqual(remaining, [queued[1], queued[2], queued[3]]);
});

test("rebasing advances only the target workspace and rejects invalid revisions", () => {
  const queue = [
    { workspace_id: 5, path: "a.js", content: "A", base_revision: 2 },
    { workspace_id: "5", path: "b.js", content: "B", base_revision: 2 },
    { workspace_id: 6, path: "a.js", content: "other", base_revision: 2 },
    { path: "legacy.js", content: "legacy" },
  ];
  assert.deepEqual(rebaseWorkspacePatches(queue, 5, "3"), [
    { ...queue[0], base_revision: 3 },
    { ...queue[1], base_revision: 3 },
    queue[2],
    queue[3],
  ]);
  assert.deepEqual(rebaseWorkspacePatches(queue, 5, "not-a-revision"), queue);
  assert.deepEqual(rebaseWorkspacePatches(queue, null, 3), queue);
});
