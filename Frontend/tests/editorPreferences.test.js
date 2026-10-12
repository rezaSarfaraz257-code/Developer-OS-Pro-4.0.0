import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_EDITOR_PREFERENCES, normalizeEditorPreferences } from "../src/ide/editorPreferences.js";
test("malformed settings safely use defaults",()=>{assert.deepEqual(normalizeEditorPreferences(null),{...DEFAULT_EDITOR_PREFERENCES});assert.deepEqual(normalizeEditorPreferences([]),{...DEFAULT_EDITOR_PREFERENCES});});
test("font size is clamped and rounded",()=>{assert.equal(normalizeEditorPreferences({fontSize:8}).fontSize,11);assert.equal(normalizeEditorPreferences({fontSize:28}).fontSize,24);assert.equal(normalizeEditorPreferences({fontSize:17.6}).fontSize,18);assert.equal(normalizeEditorPreferences({fontSize:"bad"}).fontSize,13);});
test("themes and indentation are allowlisted",()=>{assert.equal(normalizeEditorPreferences({theme:"hc-black",tabSize:4}).theme,"hc-black");assert.equal(normalizeEditorPreferences({theme:"remote-url",tabSize:8}).theme,"developer-os-dark");assert.equal(normalizeEditorPreferences({tabSize:"4"}).tabSize,4);});
test("boolean settings are normalized",()=>{const s=normalizeEditorPreferences({wordWrap:1,minimap:false,lineNumbers:false,stickyScroll:false,formatOnType:false});assert.equal(s.wordWrap,false);assert.equal(s.minimap,false);assert.equal(s.lineNumbers,false);assert.equal(s.stickyScroll,false);assert.equal(s.formatOnType,false);});

test("advanced editor preferences default safely and retain legacy compatibility",()=>{
 const legacy=normalizeEditorPreferences({fontSize:15,minimap:false});
 assert.equal(legacy.fontSize,15);
 assert.equal(legacy.minimap,false);
 assert.equal(legacy.fontLigatures,false);
 assert.equal(legacy.smoothCaret,true);
 assert.equal(legacy.renderWhitespace,"selection");
 assert.equal(legacy.codeLens,true);
});
test("advanced editor preferences allow only supported values",()=>{
 const valid=normalizeEditorPreferences({fontLigatures:true,smoothCaret:false,renderWhitespace:"all",codeLens:false});
 assert.equal(valid.fontLigatures,true);
 assert.equal(valid.smoothCaret,false);
 assert.equal(valid.renderWhitespace,"all");
 assert.equal(valid.codeLens,false);
 const invalid=normalizeEditorPreferences({renderWhitespace:"sometimes"});
 assert.equal(invalid.renderWhitespace,"selection");
});

test("editor ergonomics preferences normalize safe defaults and explicit choices",()=>{
 const defaults=normalizeEditorPreferences({});
 assert.equal(defaults.smoothScrolling,true);
 assert.equal(defaults.bracketPairGuides,true);
 assert.equal(defaults.mouseWheelZoom,false);
 assert.equal(defaults.renderLineHighlight,"line");
 const chosen=normalizeEditorPreferences({smoothScrolling:false,bracketPairGuides:false,mouseWheelZoom:true,renderLineHighlight:"gutter"});
 assert.equal(chosen.smoothScrolling,false);
 assert.equal(chosen.bracketPairGuides,false);
 assert.equal(chosen.mouseWheelZoom,true);
 assert.equal(chosen.renderLineHighlight,"gutter");
});
test("line highlight mode is allowlisted",()=>{
 assert.equal(normalizeEditorPreferences({renderLineHighlight:"all"}).renderLineHighlight,"all");
 assert.equal(normalizeEditorPreferences({renderLineHighlight:"script"}).renderLineHighlight,"line");
});
