import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const testsDir = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(testsDir, "../src");

const criticalStyles = [
  "index.css",
  "ProductPolish2026.css",
  "AppAccessibility2026.css",
  "ScrollbarBehavior.css",
  "ide/IDEStudio2026.css",
];

test("all critical frontend entrypoint stylesheets exist", () => {
  for (const stylesheet of criticalStyles) {
    const filePath = path.join(srcDir, stylesheet);
    assert.equal(fs.existsSync(filePath), true, `missing stylesheet: ${stylesheet}`);
  }
});

test("app entrypoint references only existing local style layers", () => {
  const entry = fs.readFileSync(path.join(srcDir, "main.jsx"), "utf8");
  const imports = [...entry.matchAll(/import\s+["'](\.[^"']+\.css)["']/g)].map((match) => match[1]);

  for (const importedPath of imports) {
    const resolved = path.resolve(srcDir, importedPath);
    assert.equal(fs.existsSync(resolved), true, `missing imported stylesheet: ${importedPath}`);
  }
});
