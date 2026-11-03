import test from "node:test";
import assert from "node:assert/strict";
import { mapLspCompletionItem, mapLspWorkspaceEdit } from "../src/ide/monacoCompletion.js";

const kindNames = [
  "Text", "Method", "Function", "Constructor", "Field", "Variable", "Class",
  "Struct", "Interface", "Module", "Property", "Event", "Operator", "Unit",
  "Value", "Constant", "Enum", "EnumMember", "Keyword", "Snippet", "Color",
  "File", "Reference", "Folder", "TypeParameter",
];
const monaco = {
  Uri: { parse: value => ({ toString: () => value }) },
  languages: {
    CompletionItemKind: Object.fromEntries(kindNames.map((name, index) => [name, index + 1])),
    CompletionItemInsertTextRule: { InsertAsSnippet: 4 },
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
