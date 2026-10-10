import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_EDITOR_PREFERENCES, normalizeEditorPreferences } from "../src/ide/editorPreferences.js";

test("editor preferences fall back safely for malformed input", () => {
  assert.deepEqual(normalizeEditorPreferences(null), { ...DEFAULT_EDITOR_PREFERENCES });
  assert.deepEqual(normalizeEditorPreferences([]), { ...DEFAULT_EDITOR_PREFERENCES });
});

test("editor font size is rounded and clamped to supported limits", () => {
  assert.equal(normalizeEditorPreferences({ fontSize: 8 }).fontSize, 11);
  assert.equal(normalizeEditorPreferences({ fontSize: 28 }).fontSize, 24);
  assert.equal(normalizeEditorPreferences({ fontSize: 17.6 }).fontSize, 18);
  assert.equal(normalizeEditorPreferences({ fontSize: "invalid" }).fontSize, 13);
});

test("only supported themes and indentation widths are accepted", () => {
  assert.equal(normalizeEditorPreferences({ theme: "hc-black", tabSize: 4 }).theme, "hc-black");
  assert.equal(normalizeEditorPreferences({ theme: "remote-url", tabSize: 8 }).theme, "developer-os-dark");
  assert.equal(normalizeEditorPreferences({ tabSize: "4" }).tabSize, 4);
});

test("boolean preferences are normalized instead of trusting persisted data", () => {
  const settings = normalizeEditorPreferences({ wordWrap: 1, minimap: false, lineNumbers: false, stickyScroll: false, formatOnType: false });
  assert.equal(settings.wordWrap, false);
  assert.equal(settings.minimap, false);
  assert.equal(settings.lineNumbers, false);
  assert.equal(settings.stickyScroll, false);
  assert.equal(settings.formatOnType, false);
});
