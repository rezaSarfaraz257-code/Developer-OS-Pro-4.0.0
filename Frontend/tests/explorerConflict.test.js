import test from "node:test";
import assert from "node:assert/strict";
import { canRetryExplorerMutation, canRetryExplorerDelete } from "../src/ide/explorerConflict.js";

test("Explorer create retry refuses a path that appeared after a revision conflict", () => {
  assert.equal(canRetryExplorerMutation({ action: "create", path: "new.js" }, {}), true);
  assert.equal(canRetryExplorerMutation({ action: "create", path: "new.js" }, { "new.js": "" }), false);
});

test("Explorer rename retry requires an unchanged source and a free destination", () => {
  const files = { "src/a.js": "original" };
  assert.equal(canRetryExplorerMutation({ action: "rename", path: "src/a.js", to: "src/b.js" }, files, "original"), true);
  assert.equal(canRetryExplorerMutation({ action: "rename", path: "src/a.js", to: "src/b.js" }, { ...files, "src/b.js": "other" }, "original"), false);
  assert.equal(canRetryExplorerMutation({ action: "rename", path: "src/a.js", to: "src/b.js" }, { "src/a.js": "changed" }, "original"), true);
  assert.equal(canRetryExplorerMutation({ action: "copy", path: "src/a.js", to: "src/b.js" }, files, "original"), false);
});

test("Explorer delete retry requires the exact original file content", () => {
  assert.equal(canRetryExplorerDelete("src/a.js", "original", { "src/a.js": "original" }), true);
  assert.equal(canRetryExplorerDelete("src/a.js", "original", { "src/a.js": "changed" }), false);
  assert.equal(canRetryExplorerDelete("src/a.js", "original", {}), false);
});
