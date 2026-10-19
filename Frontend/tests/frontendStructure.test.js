import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseOfflineQueue, queueWorkspacePatch, getWorkspacePatches, removeWorkspacePatches } from "../src/ide/offlineQueue.js";

const testsDir = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(testsDir, "../src");

const criticalStyles = [
  "index.css",
  "ExploreModern.css",
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
  assert.match(reporter, /split\(\/\[\?#\]\/\s*,\s*1\)/);
});

test("production error boundary hides raw diagnostics and logs only incident metadata", () => {
  const boundary = fs.readFileSync(path.resolve(srcDir, "components/ErrorBoundary.jsx"), "utf8");

  assert.match(boundary, /message:\s*isDevelopment\s*\?/);
  assert.match(boundary, /stack:\s*isDevelopment\s*\?/);
  assert.match(boundary, /import\.meta\.env\.DEV\s*&&\s*\(/);
  assert.match(boundary, /console\.error\("Developer OS UI error",\s*\{\s*incidentId:\s*errorId\s*\}\)/);
});

test("Explore favors practical SaaS usability over decorative game-like effects", () => {
  const explore = fs.readFileSync(path.resolve(srcDir, "ExploreModern.css"), "utf8");
  assert.match(explore, /Practical SaaS pass: prioritize readable content and task completion/);
  assert.match(explore, /\.explore-modern \.explore-orbit \{ display: none; \}/);
  assert.match(explore, /\.explore-modern \.explore-card:hover\s*\{\s*transform: none;/);
  assert.match(explore, /\.explore-modern \.explore-hero h1 span\s*\{[\s\S]*-webkit-text-fill-color: currentColor;/);
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

test("web app document and install manifest keep essential metadata valid", () => {
  const html = fs.readFileSync(path.resolve(srcDir, "../index.html"), "utf8");
  const manifestPath = path.resolve(srcDir, "../public/app-manifest.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));

  assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/);
  assert.match(html, /<meta name="description"/);
  assert.match(html, /<link rel="manifest" href="\/app-manifest\.json"/);
  assert.equal(manifest.name, "Developer OS");
  assert.equal(manifest.start_url, "/");
  assert.equal(manifest.scope, "/");
  assert.equal(manifest.display, "standalone");
  assert.ok(Array.isArray(manifest.icons) && manifest.icons.length > 0);
});

test("command surface polish is additive and respects reduced-motion preferences", () => {
  const polish = fs.readFileSync(path.resolve(srcDir, "ProductPolish2026.css"), "utf8");

  assert.match(polish, /\.os-shell\s*\{/);
  assert.match(polish, /\.os-rail \.rail-item\.active/);
  assert.match(polish, /\.os-main \.global-search:focus-within/);
  assert.match(polish, /\.command-center-page \.cc-hero-v2/);
  assert.match(polish, /\.command-center-page \.cc-surface/);
  assert.match(polish, /@keyframes dos-page-enter/);
  assert.match(polish, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.os-main \.page/);
});

test("Web IDE responsive layout preserves editor space and mobile Explorer access", () => {
  const idePolish = fs.readFileSync(path.resolve(srcDir, "ide/IDEStudio2026.css"), "utf8");

  assert.match(idePolish, /\.dos-ide-pro \.dos-body:not\(\.dos-explorer-collapsed\) \.dos-explorer/);
  assert.match(idePolish, /position:\s*absolute[\s\S]*width:\s*min\(290px, calc\(100% - 54px\)\)/);
  assert.match(idePolish, /\.dos-ide-pro\.dos-focus-mode \.dos-body/);
  assert.match(idePolish, /\.dos-dock-tabs[\s\S]*overflow-x:\s*auto/);
  assert.match(idePolish, /\.dos-statusbar[\s\S]*overflow:\s*hidden/);
  assert.match(idePolish, /@media \(prefers-reduced-motion: reduce\)[\s\S]*animation:\s*none/);
});

test("command palette filters commands and executes the first keyboard match", () => {
  const app = fs.readFileSync(path.resolve(srcDir, "App.jsx"), "utf8");

  assert.match(app, /const filteredCommands = useMemo\(\(\) =>/);
  assert.match(app, /commands\.filter\(\(\[label, path\]\)/);
  assert.match(app, /if \(paletteOpen\)\s*\{[\s\S]*filteredCommands\[0\]/);
  assert.match(app, /id="command-list"/);
  assert.match(app, /No matching commands/);
  assert.match(app, /aria-label=\{paletteOpen \? "Filter commands" : "Search everything"\}/);
  assert.match(app, /aria-label="Notifications" aria-expanded=\{openNotif\}/);
  assert.match(app, /event\.key === "Escape"\) \{ setPaletteOpen\(false\); setOpenNotif\(false\); setSearch\(""); \}/);
});

test("Web IDE keeps mobile actions reachable and supports high-contrast keyboard focus", () => {
  const idePolish = fs.readFileSync(path.resolve(srcDir, "ide/IDEStudio2026.css"), "utf8");

  assert.match(idePolish, /\.dos-ide-pro \.dos-top-actions[\s\S]*max-width:\s*48vw[\s\S]*overflow-x:\s*auto/);
  assert.doesNotMatch(idePolish, /\.dos-ide-pro \.dos-top-actions button:nth-child\(n\+4\)\s*\{\s*display:\s*none/s);
  assert.match(idePolish, /:focus-visible\s*\{\s*outline:\s*2px solid/);
  assert.match(idePolish, /@media \(forced-colors: active\)/);
});

test("offline queue scopes edits to their originating workspace", () => {
  const queue = [
    { path: "src/app.js", content: "workspace A", workspace_id: "1" },
    { path: "src/app.js", content: "workspace B", workspace_id: "2" },
    { path: "legacy.txt", content: "unscoped legacy edit" },
  ];
  const workspaceA = getWorkspacePatches(queue, 1);
  assert.equal(workspaceA.length, 1);
  assert.equal(workspaceA[0].content, "workspace A");

  const remaining = removeWorkspacePatches(queue, 1);
  assert.deepEqual(remaining.map((item) => item.content), ["workspace B", "unscoped legacy edit"]);
});

test("offline queue replaces only the same path in the same workspace", () => {
  const queue = [
    { path: "src/app.js", content: "old A", workspace_id: "1" },
    { path: "src/app.js", content: "B", workspace_id: "2" },
    { path: "legacy.txt", content: "legacy" },
  ];
  const next = queueWorkspacePatch(queue, {
    path: "src/app.js",
    content: "new A",
    workspace_id: "1",
  });

  assert.equal(next.length, 3);
  assert.equal(next.find((item) => item.workspace_id === "1").content, "new A");
  assert.equal(next.find((item) => item.workspace_id === "2").content, "B");
  assert.ok(next.some((item) => item.path === "legacy.txt"));
});

test("offline queue parsing retains identifiable legacy edits and ignores malformed entries", () => {
  const queue = parseOfflineQueue(JSON.stringify([
    { path: "old.txt", content: "keep for recovery" },
    { path: "new.txt", content: "scoped", workspace_id: "7" },
    { path: 4, content: "invalid path" },
    null,
  ]));

  assert.equal(queue.length, 2);
  assert.equal(queue[0].workspace_id, undefined);
  assert.equal(queue[0].content, "keep for recovery");
  assert.deepEqual(parseOfflineQueue("not-json"), []);
});
