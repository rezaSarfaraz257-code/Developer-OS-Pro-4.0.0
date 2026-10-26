import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseOfflineQueue, queueWorkspacePatch, getWorkspacePatches, removeWorkspacePatches, canSafelySyncOfflinePatches, removeSyncedWorkspacePatches, rebaseWorkspacePatches } from "../src/ide/offlineQueue.js";
import { appendBoundedText, DEFAULT_TERMINAL_BUFFER_LIMIT } from "../src/ide/terminalBuffer.js";
import { canAcknowledgeWorkspaceEdit } from "../src/ide/collaborationState.js";

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
  assert.ok(app.includes('if (event.key === "Escape") { setPaletteOpen(false); setOpenNotif(false); setSearch(""); }'));
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

test("Web IDE routes tab switching through the save guard and refuses false save success", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /onClick=\{\(\)=>\{void open\(p\);setRecent/);
  assert.match(ide, /if\(savingRef\.current\)\{setStatus\("Save already in progress; wait before switching files\."\);return false;\}/);
  assert.match(ide, /Newer edits remain unsaved/);
  assert.match(ide, /return !hasNewerLocalEdit/);
  assert.match(ide, /if\(\(dirtyPathsRef\.current\.has\(refs\.active\.current\)\|\|refs\.dirty\.current\)&&p!==refs\.active\.current\)\{const saved=await save\(true\);if\(!saved\)return;\}/);
  assert.match(ide, /clearTimeout\(refs\.timer\.current\);const pendingEditTimer=editTimersRef\.current\.get\(p\)/);
});

test("Web IDE tracks split-pane edits per file and saves every dirty file", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /dirtyPathsRef=useRef\(new Set\(\)\)/);
  assert.match(ide, /dirtyPathsRef\.current\.has\(d\.path\)/);
  assert.match(ide, /onChange=\{v=>edit\(v,secondary\)\}/);
  assert.match(ide, /const pending=\[\.\.\.dirtyPathsRef\.current\]/);
  assert.match(ide, /const saveFile=useCallback\(async\(silent=false,targetPath=refs\.active\.current\)/);
  assert.match(ide, /for\(const dirtyPath of dirtyPathsRef\.current\)/);
  assert.match(ide, /sentPatchContentsRef\.current\.get\(ackPath\)/);
});

test("Web IDE bounds streaming terminal output for long-running sessions", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /appendBoundedText\(t,d\.data\|\|""\)/);
  const limit = 12;
  assert.equal(appendBoundedText("abcdefgh", "ijklmnop", limit), "efghijklmnop");
  assert.equal(appendBoundedText("older output", "new", 5), "utnew");
  assert.equal(DEFAULT_TERMINAL_BUFFER_LIMIT, 250_000);
});


test("collaboration acknowledgements cannot clear newer or cross-workspace edits", () => {
  const base = {
    activeWorkspaceId: 7,
    ackWorkspaceId: "7",
    ackPath: "src/app.js",
    currentContent: "saved content",
    ackContent: "saved content",
    hasPendingEdit: false,
    saving: false,
  };

  assert.equal(canAcknowledgeWorkspaceEdit(base), true);
  assert.equal(canAcknowledgeWorkspaceEdit({ ...base, currentContent: "newer local edit" }), false);
  assert.equal(canAcknowledgeWorkspaceEdit({ ...base, hasPendingEdit: true }), false);
  assert.equal(canAcknowledgeWorkspaceEdit({ ...base, saving: true }), false);
  assert.equal(canAcknowledgeWorkspaceEdit({ ...base, ackWorkspaceId: "8" }), false);
  assert.equal(canAcknowledgeWorkspaceEdit({ ...base, ackContent: undefined }), false);

  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /canAcknowledgeWorkspaceEdit\(/);
  assert.match(ide, /const ack=d\.event\|\|\{\}/);
  assert.match(ide, /hasPendingEdit:editTimersRef\.current\.has\(ackPath\)/);
  assert.match(ide, /saving:savingRef\.current/);
});


test("offline replay is allowed only against its exact base revision", () => {
  assert.equal(canSafelySyncOfflinePatches([
    { path: "a.js", content: "A", base_revision: 12 },
    { path: "b.js", content: "B", base_revision: 12 },
  ], 12), true);
  assert.equal(canSafelySyncOfflinePatches([{ path: "a.js", base_revision: 12 }], 13), false);
  assert.equal(canSafelySyncOfflinePatches([
    { path: "a.js", base_revision: 12 },
    { path: "b.js", base_revision: 13 },
  ], 12), false);
  assert.equal(canSafelySyncOfflinePatches([{ path: "a.js" }], 12), false);
  assert.equal(canSafelySyncOfflinePatches([{ path: "a.js", base_revision: null }], 0), false);
  assert.equal(canSafelySyncOfflinePatches([{ path: "a.js", base_revision: "" }], 0), false);
  assert.equal(canSafelySyncOfflinePatches([], 12), false);
});


test("offline replay removes only exact synced entries and retains newer edits", () => {
  const original = { workspace_id: "7", path: "src/app.js", content: "old", base_revision: 4 };
  const newer = { workspace_id: "7", path: "src/app.js", content: "newer", base_revision: 4 };
  const otherWorkspace = { workspace_id: "8", path: "src/app.js", content: "other", base_revision: 4 };
  const legacy = { path: "legacy.txt", content: "recover me" };
  const remaining = removeSyncedWorkspacePatches([original, newer, otherWorkspace, legacy], [original]);
  assert.deepEqual(remaining, [newer, otherWorkspace, legacy]);
});


test("offline sync checkpoints successful files and rebases only the same workspace", () => {
  const queue = [
    { workspace_id: "7", path: "a.js", content: "saved", base_revision: 4 },
    { workspace_id: "7", path: "b.js", content: "pending", base_revision: 4 },
    { workspace_id: "8", path: "a.js", content: "other workspace", base_revision: 9 },
    { path: "legacy.txt", content: "recover me" },
  ];
  const afterFirstSave = removeSyncedWorkspacePatches(queue, [queue[0]]);
  const rebased = rebaseWorkspacePatches(afterFirstSave, "7", 5);
  assert.deepEqual(rebased, [
    { workspace_id: "7", path: "b.js", content: "pending", base_revision: 5 },
    { workspace_id: "8", path: "a.js", content: "other workspace", base_revision: 9 },
    { path: "legacy.txt", content: "recover me" },
  ]);
  assert.deepEqual(rebaseWorkspacePatches(queue, "7", Number.NaN), queue);
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /refs\.dirtyBaseRevision\.current=anyDirty\?revision:null/);
});


test("execution output is bounded and stale run responses cannot update the IDE", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /if\(runId!==executionRunRef\.current\)return;/);
  assert.match(ide, /setTerminal\(t=>appendBoundedText\(t,od\.output\)\)/);
  assert.match(ide, /setTerminal\(t=>appendBoundedText\(t,initial\+"\\n"\)\)/);
  assert.match(ide, /if\(d\.files\)\{const serverFiles=\{\.\.\.d\.files\};for\(const dirtyPath of dirtyPathsRef\.current\)/);
  assert.match(ide, /async function install\(\).*const saved=await save\(true\);if\(!saved\)return/s);
  assert.match(ide, /setTerminal\(appendBoundedText\("",d\.output\|\|"Install completed\\n"\)\)/);
});

test("IDE WebSocket handlers ignore stale connections and bound terminal status output", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /if\(terminalSocket\.current!==socket\)return;/);
  assert.match(ide, /if\(collab\.current!==socket\)return;/);
  assert.match(ide, /setTerminal\(t=>appendBoundedText\(t,/);
  assert.match(ide, /if\(terminalSocket\.current===socket\)setStatus\("Terminal connection error"\)/);
  assert.match(ide, /if\(collab\.current===socket\)\{setCollabState\("offline"\);setStatus\("Collaboration connection error"\)\}/);
});


test("IDE diagnostics ignore stale responses after workspace, file, or content changes", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /diagnosticsRequestRef=useRef\(0\)/);
  assert.match(ide, /requestId!==diagnosticsRequestRef\.current/);
  assert.match(ide, /String\(refs\.ws\.current\?\.id\)!==String\(workspace\.id\)/);
  assert.match(ide, /refs\.active\.current!==p\|\|refs\.files\.current\[p\]!==source/);
  assert.match(ide, /diagnosticsRequestRef\.current\+=1;setDiagnostics\(\[\]\)/);
});


test("IDE workspace loads ignore out-of-order responses", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /workspaceLoadRef=useRef\(0\)/);
  assert.match(ide, /const requestId=\+\+workspaceLoadRef\.current/);
  assert.match(ide, /if\(!saved\|\|requestId!==workspaceLoadRef\.current\)return/);
  assert.match(ide, /if\(requestId!==workspaceLoadRef\.current\)return/);
  assert.match(ide, /if\(requestId===workspaceLoadRef\.current\)\{const error=e\.message\|\|"Explorer load failed";setWorkspaceLoadError\(error\);setStatus\(error\)\}/);
});


test("IDE workspace deletion saves dirty files and invalidates stale requests", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /if\(dirtyPathsRef\.current\.size\|\|refs\.dirty\.current\)\{const saved=await save\(true\);if\(!saved\)return;\}if\(!confirm\(`Delete workspace/);
  assert.match(ide, /workspaceLoadRef\.current\+=1;diagnosticsRequestRef\.current\+=1;setDiagnostics\(\[\]\)/);
  assert.match(ide, /if\(String\(refs\.ws\.current\?\.id\)===deletingWorkspaceId\)setStatus/);
  assert.match(ide, /refs\.dirtyBaseRevision\.current=null;dirtyPathsRef\.current\.clear\(\)/);
});


test("IDE bulk tab closing saves dirty files before hiding tabs", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /async function closeOthers\(p=active\)\{\s*if\(dirtyPathsRef\.current\.size\|\|refs\.dirty\.current\)\{const saved=await save\(true\);if\(!saved\)return;\}/);
  assert.match(ide, /async function closeAll\(\)\{\s*if\(dirtyPathsRef\.current\.size\|\|refs\.dirty\.current\)\{const saved=await save\(true\);if\(!saved\)return;\}/);
  assert.match(ide, /const remaining=pinned\.filter\(x=>typeof refs\.files\.current\[x\]==="string"\)/);
  assert.match(ide, /if\(fallback\)\{await open\(fallback\);\}else\{setActive\(""\);refs\.active\.current=""/);
});


test("IDE tabs show unsaved state for each dirty file, including inactive tabs", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const css = fs.readFileSync(path.join(srcDir, "ProIDE.css"), "utf8");
  assert.match(ide, /dirtyPathsRef\.current\.has\(p\)\?<em className="dos-tab-dirty" title="Unsaved changes" aria-label="Unsaved changes">●<\/em>:null/);
  assert.match(ide, /pinned\.includes\(p\)\?<em className="dos-tab-pin" title="Pinned tab" aria-label="Pinned tab">◆<\/em>:null/);
  assert.match(css, /\.dos-tab \.dos-tab-dirty\{color:#ff7a90;margin-left:0;font-size:9px\}/);
});


test("IDE closing an individual inactive dirty tab saves before removing it", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /if\(dirtyPathsRef\.current\.has\(p\)\|\|\(p===active&&refs\.dirty\.current\)\)\{const saved=await save\(true\);if\(!saved\)return;\}const index=tabs\.indexOf\(p\)/);
});


test("save rechecks dirty tabs added while an earlier tab is saving", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const save = ide.match(/const save=useCallback\(async\(silent=false\)=>\{([\s\S]*?)\n \},\[saveFile\]\)/)?.[1] || "";

  assert.match(save, /for\(let pass=0;pass<10;pass\+\+\)/);
  assert.match(save, /const pending=\[\.\.\.dirtyPathsRef\.current\]/);
  assert.match(save, /pending\.length===0&&!refs\.dirty\.current&&dirtyPathsRef\.current\.size===0/);
  assert.match(save, /Edits are still changing\. Save again before continuing\./);
  assert.match(save, /if\(!ok\)return false/);
});


test("Explorer move, duplicate, and rename preserve every dirty editor tab before replacing workspace state", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const move = ide.match(/async function movePath\(from,toFolder\)\{([\s\S]*?)\n \}/)?.[1] || "";
  const duplicate = ide.match(/async function duplicatePath\(path\)\{([\s\S]*?)\n \}/)?.[1] || "";
  const rename = ide.match(/async function renamePath\(path\)\{([\s\S]*?)\n \}/)?.[1] || "";

  for (const [name, body] of [["move", move], ["duplicate", duplicate], ["rename", rename]]) {
    assert.match(body, /dirtyPathsRef\.current\.size\|\|refs\.dirty\.current/, `${name} must detect inactive dirty tabs too`);
    assert.match(body, /const saved=await save\(true\);if\(!saved\)/, `${name} must stop if saving pending edits fails`);
    assert.ok(body.indexOf("await save(true)") < body.indexOf("await applyExplorerState") || !body.includes("await applyExplorerState"), `${name} must save before replacing workspace state`);
  }
  assert.ok(duplicate.indexOf("await save(true)") < duplicate.indexOf("const content=refs.files.current[path]"), "duplicate must read the latest source contents after saving dirty edits");
});


test("IDE workspace-changing and execution actions account for inactive dirty tabs", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const load = ide.match(/const load=useCallback\(async\(item\)=>\{([\s\S]*?)\n/ )?.[1] || "";
  const folder = ide.match(/async function newFolder\(\)\{([\s\S]*?)\n \}/)?.[1] || "";
  const remove = ide.match(/async function removeFile\(\)\{([\s\S]*?)\n \}/)?.[1] || "";
  const install = ide.match(/async function install\(\)\{([\s\S]*?)\n/ )?.[1] || "";
  assert.match(load, /dirtyPathsRef\.current\.size\|\|refs\.dirty\.current/);
  assert.match(folder, /dirtyPathsRef\.current\.size\|\|refs\.dirty\.current/);
  assert.match(remove, /dirtyPathsRef\.current\.size\|\|refs\.dirty\.current/);
  assert.match(install, /dirtyPathsRef\.current\.size\|\|refs\.dirty\.current/);
  assert.match(ide, /lastRunnerBlockRef\.current="";\s*if\(dirtyPathsRef\.current\.size\|\|refs\.dirty\.current\)\{const saved=await save\(true\);if\(!saved\)return;\}/);
});

test("active profile route exposes retryable accessible load errors instead of an endless spinner", () => {
  const app = fs.readFileSync(path.resolve(srcDir, "App.jsx"), "utf8");
  const profileStart = app.indexOf("function Profile({ onProfileUpdate }) {");
  const settingsStart = app.indexOf("function Settings({ go }) {", profileStart);
  const profile = app.slice(profileStart, settingsStart);

  assert.ok(profileStart >= 0 && settingsStart > profileStart, "active Profile component should be present");
  assert.match(profile, /const \[loadError, setLoadError\] = useState\(""/);
  assert.match(profile, /\.then\(async response => \{/);
  assert.match(profile, /if \(!response\.ok\) \{/);
  assert.match(profile, /formatApiError\(data, "Could not load your profile\. Please try again\."\)/);
  assert.match(profile, /\.catch\(error => \{\s*if \(active\) setLoadError\(error\.message/);
  assert.match(profile, /if \(!profile\) return loadError \?/);
  assert.match(profile, /role="alert"/);
  assert.match(profile, /setLoadAttempt\(attempt => attempt \+ 1\)/);
  assert.match(profile, /role="status" aria-live="polite"/);
  assert.doesNotMatch(profile, /setSaveError\("Could not load your profile/);
});

test("app bootstrap preserves authentication on transient profile failures and offers retry", () => {
  const app = fs.readFileSync(path.resolve(srcDir, "App.jsx"), "utf8");
  const bootstrap = app.slice(app.indexOf("export default function App() {"));

  assert.match(bootstrap, /const \[bootError,setBootError\]=useState\(""/);
  assert.match(bootstrap, /\.then\(async response=>\{/);
  assert.match(bootstrap, /if\(!response\.ok\) throw new Error\(formatApiError\(profile,"Developer OS could not initialize your profile\. Please retry\."\)\)/);
  assert.match(bootstrap, /\.catch\(error=>\{if\(active\)setBootError/);
  assert.doesNotMatch(bootstrap, /\.catch\(\(\)=>setAuthenticated\(false\)\)/);
  assert.match(bootstrap, /Retry connection/);
  assert.match(bootstrap, /role="alert" aria-live="assertive"/);
  assert.match(bootstrap, /return\(\)=>\{active=false;\}/);
});

test("community feed distinguishes initial API failure from a genuinely empty feed", () => {
  const feed = fs.readFileSync(path.resolve(srcDir, "CommunityFeed.jsx"), "utf8");

  assert.match(feed, /posts\.length === 0 && error \?/);
  assert.match(feed, /Community feed unavailable/);
  assert.match(feed, /role="alert"/);
  assert.match(feed, /Retry loading posts/);
  assert.match(feed, /onClick=\{\(\) => void loadPosts\(\)\}/);
  assert.match(feed, /The next great idea could be yours/);
});

test("Explore workflow cards describe selection rather than claiming execution", () => {
  const app = fs.readFileSync(path.resolve(srcDir, "App.jsx"), "utf8");
  const workflowCard = app.slice(app.indexOf('tab==="workflows"&&visibleWorkflows.map'), app.indexOf('tab==="resources"&&visibleResources.map'));

  assert.match(workflowCard, /SELECT PLAYBOOK/);
  assert.match(workflowCard, /aria-pressed=\{selectedWorkflowId===w\.id\}/);
  assert.match(workflowCard, /no workflow has been executed/);
  assert.doesNotMatch(workflowCard, />START PLAYBOOK</);
});

test("notification read action reports API failures instead of clearing unread state", () => {
  const app = fs.readFileSync(path.resolve(srcDir, "App.jsx"), "utf8");
  assert.match(app, /const markNotificationsRead = async \(\) =>/);
  assert.match(app, /if \(!response\.ok\) throw new Error\("Notifications could not be marked as read/);
  assert.match(app, /catch \(error\) \{\s*setNotificationActionError/);
  assert.match(app, /setNotifications\(previous => \(\{ \.\.\.previous, unread: 0 \}\)\)/);
  assert.match(app, /notificationActionBusy \|\| !notifications\.unread/);
  assert.match(app, /notificationActionError && <p role="alert">/);
  assert.match(app, /onClick=\{markNotificationsRead\}/);
});

test("IDE workspace load failures expose an accessible retry action", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  assert.match(ide, /\[workspaceLoadError,setWorkspaceLoadError\]=useState\(""/);
  assert.match(ide, /setWorkspaceLoadError\(error\);setStatus\(error\)/);
  assert.match(ide, /role=\{workspaceLoadError\?"alert":"status"\}/);
  assert.match(ide, /workspaceLoadError&&<button type="button" className="dos-status-retry" onClick=\{\(\)=>void load\(ws\|\|workspaces\[0\]\)\}/);
  assert.ok(ide.includes('setWorkspaceLoadError("");setWorkspaceLoading(true);setStatus("Loading workspace…")'));
  assert.match(ide, /setDirty\(false\);setWorkspaceLoadError\(""\);setStatus\("Explorer synced/);
});


test("IDE workspace retry is pending-aware and clears loading only for the current request", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const load = ide.match(/const load=useCallback\(async\(item\)=>\{([\s\S]*?)\},\[save\]\)/)?.[1] || "";
  assert.match(ide, /\[workspaceLoading,setWorkspaceLoading\]=useState\(false\)/);
  assert.match(load, /setWorkspaceLoading\(true\);setStatus\("Loading workspace…"/);
  assert.match(load, /finally\{if\(requestId===workspaceLoadRef\.current\)setWorkspaceLoading\(false\)\}/);
  assert.match(ide, /disabled=\{workspaceLoading\|\|\(!ws&&!workspaces\.length\)\}/);
  assert.match(ide, /workspaceLoading\?"Loading workspace…":"Retry workspace"/);
});

test("Explorer mutation refreshes authoritative files before replacing local state when response omits files", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const start = ide.indexOf("async function applyExplorerState(");
  const end = ide.indexOf("async function movePath(", start);
  const apply = ide.slice(start, end);
  assert.match(apply, /let next=response\?\.files&&typeof response\.files==="object"\?response\.files:null/);
  assert.match(apply, /if\(!next\)\{/);
  assert.match(apply, /const refresh=await apiFetch\(/);
  assert.match(apply, /if\(!refresh\.ok\|\|!latest\.files\|\|typeof latest\.files!=="object"\)throw Error/);
  assert.match(apply, /next=latest\.files/);
  assert.doesNotMatch(apply, /const next=d\?\.files&&typeof d\.files==="object"\?d\.files:\{\}/);
});

test("creating a folder preserves the existing Explorer tree when mutation response omits files", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const start = ide.indexOf("async function newFolder()");
  const end = ide.indexOf("async function newFile()", start);
  const createFolder = ide.slice(start, end);
  assert.match(createFolder, /const d=await explorerMutation\(\{path:folder\+"\/\.gitkeep",content:"",action:"create"\}\)/);
  assert.match(createFolder, /d\.files&&typeof d\.files==="object"\?d\.files:\{\.\.\.refs\.files\.current,\[folder\+"\/\.gitkeep"\]:""\}/);
  assert.doesNotMatch(createFolder, /const next=d\.files\|\|\{\}/);
});

test("IDE command submission without a workspace reports that the command was not sent", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const start = ide.indexOf("async function run(");
  const end = ide.indexOf("const onSaveKey", start);
  const run = ide.slice(start, end > start ? end : start + 5000);
  assert.match(run, /if\(!ws\)\{const message="No workspace selected\./);
  assert.match(run, /setStatus\("Command not sent · no workspace"\)/);
  assert.match(run, /\[RUNNER\] \$\{message\}/);
  assert.doesNotMatch(run, /if\(!ws\|\|!c\)return/);
});

test("IDE focus mode expands the editor grid and hides side panels without unmounting editor state", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const css = fs.readFileSync(path.join(srcDir, "ProIDE.css"), "utf8");
  assert.match(ide, /focusMode\?"dos-focus-mode":""/);
  assert.match(ide, /aria-label=\{focusMode\?"Exit Focus Mode":"Focus code editor"\}/);
  assert.match(css, /\.dos-ide-pro\.dos-focus-mode \.dos-body\s*\{\s*grid-template-columns:\s*48px minmax\(0,1fr\);\s*\}/);
  assert.match(css, /\.dos-ide-pro\.dos-focus-mode \.dos-body > \.dos-explorer,\s*\.dos-ide-pro\.dos-focus-mode \.dos-body > \.dos-inspector\s*\{\s*display:\s*none;\s*\}/);
  assert.match(css, /\.dos-ide-pro\.dos-focus-mode \.dos-activity/);
});

test("IDE rejects malformed array payloads when refreshing workspace file maps", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const start = ide.indexOf("async function applyExplorerState(d,");
  const end = ide.indexOf("\n async function movePath(", start);
  const refresh = ide.slice(start, end > start ? end : start + 2600);

  assert.ok(start >= 0, "Explorer state reconciliation should exist");
  assert.match(refresh, /!Array\.isArray\(response\.files\)/);
  assert.match(refresh, /!Array\.isArray\(latest\.files\)/);
  assert.match(refresh, /Workspace refresh failed after file operation/);
});


test("IDE workspace and framework list failures are visible and retryable", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const initStart = ide.indexOf('Promise.all([apiFetch("/ide/workspaces/")');
  const init = ide.slice(initStart, initStart + 1800);
  assert.match(init, /if\(!a\.ok\)throw Error\(w\.error\|\|w\.detail\|\|`Workspace list failed \(HTTP \$\{a\.status\}\)`\)/);
  assert.match(init, /if\(!b\.ok\)throw Error\(f\.error\|\|f\.detail\|\|`Framework list failed \(HTTP \$\{b\.status\}\)`\)/);
  assert.match(init, /setWorkspaceListError\(error\);setStatus\(error\)/);
  assert.match(init, /\[message,workspaceListRetry\]/);
  assert.match(ide, /workspaceListError&&<span className="dos-workspace-load-error" role="alert">/);
  assert.match(ide, /Retry workspaces/);
  assert.match(ide, /setWorkspaceListRetry\(value=>value\+1\)/);
});


test("offline collaboration sync cannot update a newly selected workspace from a stale socket", () => {
  const ide = fs.readFileSync(path.join(srcDir, "ProIDE.jsx"), "utf8");
  const start = ide.indexOf("const workspacePatches=getWorkspacePatches(q,ws.id)");
  const end = ide.indexOf("}catch(err){if(collab.current===socket)setStatus(", start);
  const sync = ide.slice(start, end > start ? end : start + 5000);

  assert.ok(start >= 0, "offline sync handler should exist");
  assert.ok(end > start, "offline sync handler should have a bounded catch");
  assert.ok(sync.indexOf("if(collab.current!==socket)return;const state=await r.json()") > sync.indexOf("const r=await apiFetch"), "stale socket must be checked after the workspace fetch");
  assert.ok(sync.indexOf("if(collab.current!==socket)return;const wr=await apiFetch") > -1, "stale socket must be checked before each queued write");
  assert.ok(sync.includes("if(collab.current===socket)setOfflineQueue(rebased);if(collab.current!==socket)return"), "stale socket must not mutate current UI state after a successful write");
  assert.ok(sync.includes("const remaining=parseOfflineQueue(localStorage.getItem(OFFLINE_QUEUE_KEY));if(collab.current!==socket)return;setOfflineQueue(remaining)"), "stale socket must not apply the old workspace revision to the active workspace");
});
