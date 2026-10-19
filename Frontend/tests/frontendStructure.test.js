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

test("runtime incident persistence excludes stack traces, messages, and query strings", () => {
  const boundary = fs.readFileSync(path.resolve(srcDir, "components/ErrorBoundary.jsx"), "utf8");
  const reporter = boundary.split("export default class ErrorBoundary")[0];

  assert.match(reporter, /route:\s*window\.location\.pathname/);
  assert.doesNotMatch(reporter, /window\.location\.search/);
  assert.doesNotMatch(reporter, /stack:\s*String\(error\?\.stack/);
  assert.doesNotMatch(reporter, /message:\s*String\(error\?\.message/);
  assert.match(reporter, /Also sanitize older entries/);
});

test("production error boundary hides raw diagnostics and logs only incident metadata", () => {
  const boundary = fs.readFileSync(path.resolve(srcDir, "components/ErrorBoundary.jsx"), "utf8");

  assert.match(boundary, /message:\s*isDevelopment\s*\?/);
  assert.match(boundary, /stack:\s*isDevelopment\s*\?/);
  assert.match(boundary, /import\.meta\.env\.DEV\s*&&\s*\(/);
  assert.match(boundary, /console\.error\("Developer OS UI error",\s*\{\s*incidentId:\s*errorId\s*\}\)/);
});

test("product panels preserve overlays and focus rings outside their bounds", () => {
  const polish = fs.readFileSync(path.resolve(srcDir, "ProductPolish2026.css"), "utf8");
  assert.match(polish, /contain:\s*layout;/);
  assert.doesNotMatch(polish, /contain:\s*layout\s+paint\s*;/);
});

test("mobile navigation and IDE activity controls keep 44px touch targets", () => {
  const accessibility = fs.readFileSync(path.resolve(srcDir, "AppAccessibility2026.css"), "utf8");
  const mobileRules = accessibility.split("@media (max-width:640px)")[1]?.split("@media")[0] || "";

  assert.match(mobileRules, /min-height:\s*44px/);
  assert.match(mobileRules, /min-width:\s*44px/);
  assert.match(mobileRules, /\.dos-ide-pro \.dos-activity button\s*\{[^}]*width:\s*44px/s);
  assert.match(mobileRules, /\.dos-ide-pro \.dos-activity button\s*\{[^}]*height:\s*44px/s);
});

test("touch devices retain visible 8px scrollbars without hover", () => {
  const scrollbars = fs.readFileSync(path.resolve(srcDir, "ScrollbarBehavior.css"), "utf8");
  const coarsePointerRules = scrollbars.split("@media (pointer: coarse)")[1]?.split("@media")[0] || "";

  assert.match(coarsePointerRules, /width:\s*8px\s*!important/);
  assert.match(coarsePointerRules, /height:\s*8px\s*!important/);
  assert.doesNotMatch(coarsePointerRules, /width:\s*6px/);
});

test("Firefox gets a usable hover/focus scrollbar fallback", () => {
  const scrollbars = fs.readFileSync(path.resolve(srcDir, "ScrollbarBehavior.css"), "utf8");
  const firefoxRules = scrollbars.split("@supports (-moz-appearance: none)")[1]?.split("/* Chromium/WebKit")[0] || "";

  assert.match(firefoxRules, /scrollbar-width:\s*thin\s*!important/);
  assert.match(firefoxRules, /\*:hover,\s*\*:focus-within\s*\{\s*scrollbar-width:\s*auto\s*!important/s);
  assert.match(firefoxRules, /\.os-rail:not\(:hover\):not\(:focus-within\)\s*\{\s*scrollbar-width:\s*none\s*!important/s);
  assert.match(firefoxRules, /@media \(pointer: coarse\)[\s\S]*scrollbar-width:\s*auto\s*!important/);
});

test("product polish includes accessible form states and mobile-safe controls", () => {
  const polish = fs.readFileSync(path.resolve(srcDir, "ProductPolish2026.css"), "utf8");

  assert.match(polish, /\.os-main :where\(input, textarea, select\):focus-visible/);
  assert.match(polish, /\.os-main :where\(button, input, textarea, select\):disabled/);
  assert.match(polish, /@media \(max-width: 640px\)[\s\S]*min-height: 44px/);
  assert.match(polish, /@media \(prefers-contrast: more\)/);
  assert.match(polish, /@media \(prefers-reduced-motion: reduce\)/);
});
