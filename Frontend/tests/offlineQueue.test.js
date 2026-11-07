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
  preserveInterruptedWorkspacePatches,
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


test("interrupted collaboration keeps the newest same-file edit and original dispatch revision", () => {
  const originalQueue = [
    { workspace_id: "other", path: "src/main.js", content: "other workspace", base_revision: 2 },
    { workspace_id: "7", path: "src/keep.js", content: "already queued", base_revision: 8 },
    { path: "legacy.js", content: "legacy patch" },
  ];
  const inFlight = {
    workspace_id: "7",
    path: "src/main.js",
    content: "older dispatched content",
    base_revision: 8,
  };
  const pending = [
    {
      workspace_id: "7",
      path: "src/main.js",
      content: "newest local content",
      base_revision: 8,
    },
  ];

  const recovered = preserveInterruptedWorkspacePatches(
    originalQueue,
    7,
    inFlight,
    pending,
  );

  assert.deepEqual(recovered, [
    ...originalQueue,
    {
      ...pending[0],
      workspace_id: "7",
      base_revision: 8,
    },
  ]);
  assert.equal(
    recovered.find((patch) => patch.workspace_id === "7" && patch.path === "src/main.js").content,
    "newest local content",
  );
  assert.equal(
    recovered.find((patch) => patch.workspace_id === "7" && patch.path === "src/main.js").base_revision,
    8,
    "uncertain in-flight writes must retain their original base revision",
  );
  assert.equal(originalQueue.length, 3, "recovery must not mutate the existing queue");
});

test("interrupted collaboration preserves independent pending files and workspace boundaries", () => {
  const queue = [
    { workspace_id: 3, path: "src/a.js", content: "old A", base_revision: 4 },
    { workspace_id: 9, path: "src/b.js", content: "other workspace", base_revision: 6 },
  ];
  const recovered = preserveInterruptedWorkspacePatches(
    queue,
    3,
    { workspace_id: 3, path: "src/a.js", content: "in-flight A", base_revision: 4 },
    [
      { workspace_id: 3, path: "src/a.js", content: "latest A", base_revision: 4 },
      { workspace_id: 3, path: "src/b.js", content: "pending B", base_revision: 4 },
    ],
  );

  assert.deepEqual(recovered, [
    { workspace_id: 9, path: "src/b.js", content: "other workspace", base_revision: 6 },
    { workspace_id: "3", path: "src/a.js", content: "latest A", base_revision: 4 },
    { workspace_id: "3", path: "src/b.js", content: "pending B", base_revision: 4 },
  ]);
});

test("interrupted collaboration safely ignores malformed patches and empty workspace IDs", () => {
  const queue = [{ workspace_id: 2, path: "keep.js", content: "keep" }];
  assert.strictEqual(
    preserveInterruptedWorkspacePatches(queue, "  ", { path: "a.js", content: "x" }, []),
    queue,
  );
  assert.deepEqual(
    preserveInterruptedWorkspacePatches(queue, 2, null, [
      null,
      { path: "missing-content.js" },
      { path: "valid.js", content: "valid", base_revision: 5 },
    ]),
    [
      queue[0],
      { path: "valid.js", content: "valid", base_revision: 5, workspace_id: "2" },
    ],
  );
});
