import test from "node:test";
import assert from "node:assert/strict";
import { commandForFile } from "../src/ide/commandForFile.js";

test("auto-run commands quote file paths for a POSIX shell", () => {
  assert.equal(commandForFile("src/main.js", { "src/main.js": "" }), "node 'src/main.js'");
  assert.equal(commandForFile("src/it's.js", { "src/it's.js": "" }), "node 'src/it'\\''s.js'");
  assert.equal(
    commandForFile("src/$(touch pwned).js", { "src/$(touch pwned).js": "" }),
    "node 'src/$(touch pwned).js'",
  );
  assert.equal(commandForFile("src/main.py", { "src/main.py": "" }), "python3 'src/main.py'");
  assert.equal(commandForFile("src/main.ts", { "src/main.ts": "" }), "npx --no-install tsx 'src/main.ts'");
});

test("auto-run rejects paths that could escape or ambiguously address workspace files", () => {
  const files = {
    "../outside.js": "",
    "/etc/passwd.js": "",
    "src//main.js": "",
    "src/../main.js": "",
    "src/main.js\nnode evil.js": "",
    "src/main.js": "",
  };
  assert.equal(commandForFile("../outside.js", files), "");
  assert.equal(commandForFile("/etc/passwd.js", files), "");
  assert.equal(commandForFile("src//main.js", files), "");
  assert.equal(commandForFile("src/../main.js", files), "");
  assert.equal(commandForFile("src/main.js\nnode evil.js", files), "");
  assert.equal(commandForFile("missing.js", files), "");
  assert.equal(commandForFile("README.md", { "README.md": "" }), "");
});
