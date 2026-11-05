import test from "node:test";
import assert from "node:assert/strict";
import { mapLspCompletionItem, mapLspWorkspaceEdit, mapLspLocations, mapLspDiagnostics, mapLspDocumentHighlights, mapLspInlayHints, mapLspSignatureHelp, mapLspDocumentSymbols, nextLspDocumentVersion, getUnsupportedLspResourceOperations } from "../src/ide/monacoCompletion.js";

const kindNames = [
  "Text", "Method", "Function", "Constructor", "Field", "Variable", "Class",
  "Struct", "Interface", "Module", "Property", "Event", "Operator", "Unit",
  "Value", "Constant", "Enum", "EnumMember", "Keyword", "Snippet", "Color",
  "File", "Reference", "Folder", "TypeParameter",
];
const monaco = {
  MarkerSeverity: { Error: 8, Warning: 4, Info: 2, Hint: 1 },
  Uri: { parse: value => ({ toString: () => value }) },
  languages: {
    CompletionItemKind: Object.fromEntries(kindNames.map((name, index) => [name, index + 1])),
    CompletionItemInsertTextRule: { InsertAsSnippet: 4 },
    DocumentHighlightKind: { Text: 0, Read: 1, Write: 2 },
    InlayHintKind: { Type: 1, Parameter: 2 },
    SymbolKind: Object.fromEntries(["File","Module","Namespace","Package","Class","Method","Property","Field","Constructor","Enum","Interface","Function","Variable","Constant","String","Number","Boolean","Array","Object","Key","Null","EnumMember","Struct","Event","Operator","TypeParameter"].map((name,index)=>[name,index])),
  },
};
const model = {
  getWordUntilPosition: () => ({ startColumn: 3, endColumn: 8 }),
};
const position = { lineNumber: 4, column: 8 };

test("LSP completion maps kind, edit range, snippets and documentation to Monaco", () => {
  const item = mapLspCompletionItem({
    label: "makeThing",
    kind: 3,
    detail: "Create a thing",
    insertTextFormat: 2,
    textEdit: {
      range: { start: { line: 3, character: 2 }, end: { line: 3, character: 7 } },
      newText: "makeThing($1)",
    },
    documentation: { kind: "markdown", value: "**Creates** a thing" },
  }, monaco, model, position);

  assert.equal(item.label, "makeThing");
  assert.equal(item.kind, monaco.languages.CompletionItemKind.Function);
  assert.equal(item.insertText, "makeThing($1)");
  assert.equal(item.insertTextRules, monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet);
  assert.deepEqual(item.range, {
    startLineNumber: 4, startColumn: 3, endLineNumber: 4, endColumn: 8,
  });
  assert.equal(item.documentation, "**Creates** a thing");
});

test("LSP completion preserves additional edits, sorting and command metadata", () => {
  const item = mapLspCompletionItem({
    label: "useThing",
    kind: 2,
    filterText: "useThingFilter",
    sortText: "001",
    preselect: true,
    commitCharacters: ["."],
    additionalTextEdits: [{
      range: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
      newText: "import Thing;\\n",
    }],
    command: { title: "Organize imports", command: "editor.action.organizeImports", arguments: ["file.js"] },
  }, monaco, model, position);

  assert.equal(item.kind, monaco.languages.CompletionItemKind.Method);
  assert.equal(item.filterText, "useThingFilter");
  assert.equal(item.sortText, "001");
  assert.equal(item.preselect, true);
  assert.deepEqual(item.commitCharacters, ["."]);
  assert.deepEqual(item.additionalTextEdits, [{
    range: { startLineNumber: 1, startColumn: 1, endLineNumber: 1, endColumn: 1 },
    text: "import Thing;\\n",
  }]);
  assert.deepEqual(item.command, {
    id: "editor.action.organizeImports",
    title: "Organize imports",
    arguments: ["file.js"],
  });
});

test("LSP insert/replace edits prefer the replacement range and plain text format", () => {
  const item = mapLspCompletionItem({
    label: "replaceMe",
    kind: 999,
    insertText: "fallback",
    textEdit: {
      insert: { start: { line: 1, character: 2 }, end: { line: 1, character: 4 } },
      replace: { start: { line: 1, character: 2 }, end: { line: 1, character: 9 } },
      newText: "replacement",
    },
    insertTextFormat: 1,
  }, monaco, model, position);

  assert.equal(item.kind, monaco.languages.CompletionItemKind.Text);
  assert.equal(item.insertText, "replacement");
  assert.deepEqual(item.range, {
    startLineNumber: 2, startColumn: 3, endLineNumber: 2, endColumn: 10,
  });
  assert.equal("insertTextRules" in item, false);
});

test("malformed LSP ranges are rejected in favor of Monaco's current word range", () => {
  const item = mapLspCompletionItem({
    label: "safeFallback",
    textEdit: {
      range: { start: { line: -1, character: 0 }, end: { line: 2, character: 4 } },
      newText: "safeFallback",
    },
  }, monaco, model, position);

  assert.deepEqual(item.range, {
    startLineNumber: 4, startColumn: 3, endLineNumber: 4, endColumn: 8,
  });
});

test("invalid completion inputs fail closed and missing edit ranges use the current word", () => {
  assert.equal(mapLspCompletionItem(null, monaco, model, position), null);
  assert.equal(mapLspCompletionItem({ label: "x" }, null, model, position), null);

  const item = mapLspCompletionItem({ label: "fallback" }, monaco, model, position);
  assert.deepEqual(item.range, {
    startLineNumber: 4, startColumn: 3, endLineNumber: 4, endColumn: 8,
  });
});

test("LSP WorkspaceEdit maps both changes and documentChanges into Monaco edits", () => {
  const workspaceEdit = mapLspWorkspaceEdit({
    changes: {
      "file:///workspace/a.py": [{
        range: { start: { line: 0, character: 4 }, end: { line: 0, character: 7 } },
        newText: "total",
      }],
    },
    documentChanges: [
      {
        textDocument: { uri: "file:///workspace/b.py", version: 3 },
        edits: [{
          range: { start: { line: 2, character: 0 }, end: { line: 2, character: 5 } },
          newText: "total",
        }],
      },
      {
        textDocument: { uri: "file:///workspace/a.py", version: 4 },
        edits: [{
          range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } },
          newText: "ignored-duplicate",
        }],
      },
    ],
  }, monaco);

  assert.equal(workspaceEdit.edits.length, 2);
  assert.deepEqual(workspaceEdit.edits.map(edit => edit.resource.toString()), [
    "file:///workspace/a.py",
    "file:///workspace/b.py",
  ]);
  assert.equal(workspaceEdit.edits[0].textEdit.text, "total");
  assert.deepEqual(workspaceEdit.edits[1].textEdit.range, {
    startLineNumber: 3, startColumn: 1, endLineNumber: 3, endColumn: 6,
  });
});

test("LSP WorkspaceEdit safely skips malformed edits and unsupported resource operations", () => {
  const workspaceEdit = mapLspWorkspaceEdit({
    documentChanges: [
      { kind: "rename", oldUri: "file:///old.py", newUri: "file:///new.py" },
      {
        textDocument: { uri: "file:///workspace/a.py", version: null },
        edits: [
          { range: { start: { line: -1, character: 0 }, end: { line: 0, character: 1 } }, newText: "bad" },
          { range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } }, newText: "good" },
        ],
      },
    ],
  }, monaco);

  assert.equal(workspaceEdit.edits.length, 1);
  assert.equal(workspaceEdit.edits[0].textEdit.text, "good");
  assert.deepEqual(mapLspWorkspaceEdit(null, monaco), { edits: [] });
});

test("LSP Location and LocationLink results map to safe Monaco navigation targets", () => {
  const locations = mapLspLocations([
    {
      uri: "file:///workspace/a.py",
      range: { start: { line: 0, character: 2 }, end: { line: 0, character: 8 } },
    },
    {
      targetUri: "file:///workspace/b.py",
      targetRange: { start: { line: 4, character: 0 }, end: { line: 7, character: 0 } },
      targetSelectionRange: { start: { line: 5, character: 3 }, end: { line: 5, character: 9 } },
    },
    {
      targetUri: "file:///workspace/invalid.py",
      targetRange: { start: { line: -1, character: 0 }, end: { line: 0, character: 1 } },
    },
    { uri: "not-a-valid-range" },
  ], monaco);

  assert.equal(locations.length, 2);
  assert.deepEqual(locations[0].range, {
    startLineNumber: 1, startColumn: 3, endLineNumber: 1, endColumn: 9,
  });
  assert.equal(locations[1].uri.toString(), "file:///workspace/b.py");
  assert.deepEqual(locations[1].range, {
    startLineNumber: 6, startColumn: 4, endLineNumber: 6, endColumn: 10,
  });
});

test("LSP location mapper handles empty and malformed responses without throwing", () => {
  assert.deepEqual(mapLspLocations(null, monaco), []);
  assert.deepEqual(mapLspLocations({ uri: "file:///a.py" }, monaco), []);
  assert.deepEqual(mapLspLocations({ uri: "file:///a.py", range: {} }, monaco), []);
  assert.deepEqual(mapLspLocations([{ uri: "file:///a.py", range: {} }], monaco), []);
});

test("LSP diagnostics map severity and source while rejecting malformed ranges", () => {
  const markers = mapLspDiagnostics([
    {
      severity: 1,
      message: "Unexpected token",
      source: "Pyright",
      code: "reportSyntaxError",
      range: { start: { line: 1, character: 2 }, end: { line: 1, character: 5 } },
    },
    {
      severity: 2,
      message: "Potential issue",
      range: { start: { line: 3, character: 0 }, end: { line: 3, character: 1 } },
    },
    { severity: 1, message: "Broken range", range: {} },
    { severity: 1, range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } } },
    null,
  ], monaco);

  assert.equal(markers.length, 2);
  assert.equal(markers[0].severity, monaco.MarkerSeverity.Error);
  assert.equal(markers[0].source, "Pyright");
  assert.equal(markers[0].code, "reportSyntaxError");
  assert.deepEqual(markers[0], {
    startLineNumber: 2, startColumn: 3, endLineNumber: 2, endColumn: 6,
    severity: monaco.MarkerSeverity.Error, message: "Unexpected token",
    source: "Pyright", code: "reportSyntaxError",
  });
  assert.equal(markers[1].severity, monaco.MarkerSeverity.Warning);
  assert.equal(markers[1].source, "LSP");
});

test("LSP diagnostics mapper handles missing payloads safely", () => {
  assert.deepEqual(mapLspDiagnostics(null, monaco), []);
  assert.deepEqual(mapLspDiagnostics({}, monaco), []);
  assert.deepEqual(mapLspDiagnostics([{ message: "x" }], monaco), []);
});

test("LSP document highlights map read/write/text kinds and valid ranges", () => {
  const highlights = mapLspDocumentHighlights([
    { kind: 1, range: { start: { line: 0, character: 1 }, end: { line: 0, character: 4 } } },
    { kind: 2, range: { start: { line: 2, character: 0 }, end: { line: 2, character: 5 } } },
    { kind: 3, range: { start: { line: 4, character: 2 }, end: { line: 4, character: 7 } } },
    { kind: 99, range: { start: { line: 5, character: 0 }, end: { line: 5, character: 2 } } },
    { kind: 1, range: { start: { line: -1, character: 0 }, end: { line: 0, character: 1 } } },
    null,
  ], monaco);

  assert.equal(highlights.length, 4);
  assert.deepEqual(highlights.map(item => item.kind), [
    monaco.languages.DocumentHighlightKind.Text,
    monaco.languages.DocumentHighlightKind.Read,
    monaco.languages.DocumentHighlightKind.Write,
    monaco.languages.DocumentHighlightKind.Text,
  ]);
  assert.deepEqual(highlights[0].range, {
    startLineNumber: 1, startColumn: 2, endLineNumber: 1, endColumn: 5,
  });
});

test("LSP document highlight mapper safely handles missing results", () => {
  assert.deepEqual(mapLspDocumentHighlights(null, monaco), []);
  assert.deepEqual(mapLspDocumentHighlights({}, monaco), []);
  assert.deepEqual(mapLspDocumentHighlights([{ range: {} }], monaco), []);
});

test("LSP inlay hints map positions, label parts, kinds and safe optional metadata", () => {
  const hints = mapLspInlayHints([
    { position: { line: 0, character: 4 }, label: "string", kind: 1, paddingRight: true, tooltip: "Inferred type" },
    { position: { line: 2, character: 1 }, label: [{ value: "count" }, { value: ": " }, { value: "number" }], kind: 2, paddingLeft: true },
    { position: { line: -1, character: 0 }, label: "invalid" },
    { position: { line: 0, character: 0 }, label: "" },
    null,
  ], monaco);
  assert.equal(hints.length, 2);
  assert.deepEqual(hints[0], {
    position: { lineNumber: 1, column: 5 }, label: "string",
    paddingLeft: false, paddingRight: true,
    kind: monaco.languages.InlayHintKind.Type, tooltip: "Inferred type",
  });
  assert.equal(hints[1].label, "count: number");
  assert.equal(hints[1].kind, monaco.languages.InlayHintKind.Parameter);
  assert.equal(hints[1].paddingLeft, true);
});
test("LSP inlay hint mapper safely handles missing or unsupported payloads", () => {
  assert.deepEqual(mapLspInlayHints(null, monaco), []);
  assert.deepEqual(mapLspInlayHints({}, monaco), []);
  assert.deepEqual(mapLspInlayHints([{ position: {}, label: "x" }], monaco), []);
});


test("LSP signature help maps overloads, parameter labels and active selections", () => {
  const mapped = mapLspSignatureHelp({
    activeSignature: 1,
    activeParameter: 8,
    signatures: [
      { label: "open(path)", documentation: "Open a file", parameters: [{ label: "path", documentation: { value: "File path" } }] },
      { label: "open(path, mode)", parameters: [{ label: [5, 9] }, { label: "mode" }] },
    ],
  });
  assert.equal(mapped.value.signatures.length, 2);
  assert.equal(mapped.value.signatures[0].documentation, "Open a file");
  assert.equal(mapped.value.signatures[0].parameters[0].documentation, "File path");
  assert.deepEqual(mapped.value.signatures[1].parameters[0].label, [5, 9]);
  assert.equal(mapped.value.activeSignature, 1);
  assert.equal(mapped.value.activeParameter, 1);
});

test("LSP signature help rejects malformed payloads and safely bounds active indexes", () => {
  assert.equal(mapLspSignatureHelp(null), null);
  assert.equal(mapLspSignatureHelp({ signatures: [{ label: "" }] }), null);
  const mapped = mapLspSignatureHelp({ activeSignature: 99, activeParameter: -4, signatures: [
    { label: "f(a)", parameters: [{ label: [2, 99] }, { label: "a" }] },
  ] });
  assert.equal(mapped.value.activeSignature, 0);
  assert.equal(mapped.value.activeParameter, 0);
  assert.deepEqual(mapped.value.signatures[0].parameters.map(item => item.label), ["a"]);
});


test("LSP document versions advance independently for each open document", () => {
  const versions = new Map([
    ["file:///workspace/a.py", 1],
    ["file:///workspace/b.py", 1],
  ]);
  assert.equal(nextLspDocumentVersion(versions, "file:///workspace/a.py"), 2);
  assert.equal(nextLspDocumentVersion(versions, "file:///workspace/b.py"), 2);
  assert.equal(nextLspDocumentVersion(versions, "file:///workspace/a.py"), 3);
  assert.equal(versions.get("file:///workspace/a.py"), 3);
  assert.equal(nextLspDocumentVersion(null, "file:///workspace/a.py"), 2);
  assert.equal(nextLspDocumentVersion(versions, ""), 2);
});

test("LSP resource operations are detected explicitly instead of being silently applied as text edits", () => {
  const operations = getUnsupportedLspResourceOperations({
    documentChanges: [
      { kind: "create", uri: "file:///workspace/new.py" },
      { kind: "rename", oldUri: "file:///workspace/old.py", newUri: "file:///workspace/new.py" },
      { kind: "delete", uri: "file:///workspace/remove.py" },
      { textDocument: { uri: "file:///workspace/main.py" }, edits: [] },
      { kind: "unknown", uri: "file:///workspace/unknown" },
      { textDocument: { uri: "file:///workspace/malformed.py" } },
      null,
    ],
  });
  assert.deepEqual(operations.map(item => item?.kind || "malformed"), ["create", "rename", "delete", "unknown", "malformed", "malformed"]);
  assert.deepEqual(getUnsupportedLspResourceOperations(null), []);
  assert.deepEqual(getUnsupportedLspResourceOperations({ documentChanges: [] }), []);
});


test("LSP document symbols map hierarchical ranges, kinds and deprecated tags", () => {
  const symbols = mapLspDocumentSymbols([{
    name: "Example",
    kind: 5,
    detail: "class Example",
    range: { start: { line: 0, character: 0 }, end: { line: 8, character: 1 } },
    selectionRange: { start: { line: 0, character: 6 }, end: { line: 0, character: 13 } },
    children: [{
      name: "run",
      kind: 6,
      tags: [1],
      range: { start: { line: 2, character: 2 }, end: { line: 4, character: 3 } },
      selectionRange: { start: { line: 2, character: 7 }, end: { line: 2, character: 10 } },
    }],
  }], monaco);

  assert.equal(symbols.length, 1);
  assert.equal(symbols[0].name, "Example");
  assert.equal(symbols[0].kind, monaco.languages.SymbolKind.Class);
  assert.deepEqual(symbols[0].range, { startLineNumber: 1, startColumn: 1, endLineNumber: 9, endColumn: 2 });
  assert.deepEqual(symbols[0].selectionRange, { startLineNumber: 1, startColumn: 7, endLineNumber: 1, endColumn: 14 });
  assert.equal(symbols[0].children[0].kind, monaco.languages.SymbolKind.Method);
  assert.deepEqual(symbols[0].children[0].tags, [1]);
});

test("LSP document symbols accept flat SymbolInformation and reject malformed entries", () => {
  const symbols = mapLspDocumentSymbols([
    {
      name: "helper",
      kind: 12,
      containerName: "module",
      location: { uri: "inmemory://developer-os/app.js", range: {
        start: { line: 1, character: 0 }, end: { line: 1, character: 6 },
      } },
    },
    { name: "broken", kind: 12, range: { start: { line: -1, character: 0 }, end: { line: 0, character: 1 } } },
    { name: "", kind: 12, range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } } },
  ], monaco);

  assert.equal(symbols.length, 1);
  assert.equal(symbols[0].name, "helper");
  assert.equal(symbols[0].containerName, "module");
  assert.equal(symbols[0].kind, monaco.languages.SymbolKind.Function);
});

test("LSP document symbols fail closed when the server result is not an array", () => {
  assert.deepEqual(mapLspDocumentSymbols(null, monaco), []);
  assert.deepEqual(mapLspDocumentSymbols([], null), []);
});
test("LSP document symbol mapping bounds oversized and deeply nested server responses", () => {
  const range = { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } };
  const oversized = Array.from({ length: 700 }, (_, index) => ({
    name: "symbol" + index, kind: 12, range,
  }));
  assert.equal(mapLspDocumentSymbols(oversized, monaco).length, 500);

  let nested = { name: "depth-40", kind: 12, range };
  for (let depth = 39; depth >= 0; depth--) {
    nested = { name: "depth-" + depth, kind: 5, range, children: [nested] };
  }
  const bounded = mapLspDocumentSymbols([nested], monaco)[0];
  let depth = 1;
  let current = bounded;
  while (current.children?.length) {
    depth += 1;
    current = current.children[0];
  }
  assert.equal(depth, 32);
});
