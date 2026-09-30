import { useEffect, useRef } from "react";
import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import cssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";

globalThis.MonacoEnvironment = {
  getWorker(_workerId, label) {
    if (label === "json") return new jsonWorker();
    if (label === "css" || label === "scss" || label === "less") return new cssWorker();
    if (label === "html" || label === "handlebars" || label === "razor") return new htmlWorker();
    if (label === "typescript" || label === "javascript") return new tsWorker();
    return new editorWorker();
  },
};

monaco.languages.typescript.javascriptDefaults.setEagerModelSync(true);
monaco.languages.typescript.typescriptDefaults.setEagerModelSync(true);
monaco.languages.typescript.javascriptDefaults.setCompilerOptions({
  target: monaco.languages.typescript.ScriptTarget.ES2022,
  allowNonTsExtensions: true,
  module: monaco.languages.typescript.ModuleKind.ESNext,
  moduleResolution: monaco.languages.typescript.ModuleResolutionKind.NodeJs,
  jsx: monaco.languages.typescript.JsxEmit.ReactJSX,
  allowJs: true,
  checkJs: false,
});
monaco.editor.defineTheme("developer-os-dark", {
  base: "vs-dark", inherit: true,
  rules: [
    { token: "comment", foreground: "5d7188" },
    { token: "keyword", foreground: "57dfff" },
    { token: "string", foreground: "8ee6b5" },
    { token: "number", foreground: "d9a7ff" },
  ],
  colors: {
    "editor.background": "#060d16", "editor.foreground": "#d5eaff",
    "editorLineNumber.foreground": "#36516a", "editorLineNumber.activeForeground": "#65e6ff",
    "editorCursor.foreground": "#55e8ff", "editor.selectionBackground": "#174363",
    "editor.inactiveSelectionBackground": "#102d44", "editor.lineHighlightBackground": "#091725",
    "editorIndentGuide.background1": "#102335", "editorIndentGuide.activeBackground1": "#24455e",
    "editorWidget.background": "#0b1622", "editorWidget.border": "#1c3c55",
  },
});

const languageForPath = (path = "") => {
  const ext = (path.split(".").pop() || "").toLowerCase();
  return ({js:"javascript",jsx:"javascript",ts:"typescript",tsx:"typescript",py:"python",html:"html",htm:"html",css:"css",scss:"scss",json:"json",md:"markdown",yaml:"yaml",yml:"yaml",sql:"sql",sh:"shell",bash:"shell",go:"go",rs:"rust",java:"java",php:"php",xml:"xml",vue:"html",c:"c",cpp:"cpp",h:"cpp",hpp:"cpp",txt:"plaintext",env:"plaintext"})[ext] || "plaintext";
};

export default function MonacoEditor({ path, value, onChange, onCursorChange }) {
  const hostRef = useRef(null);
  const editorRef = useRef(null);
  const modelRef = useRef(null);
  const changeRef = useRef(onChange);
  const cursorRef = useRef(onCursorChange);
  changeRef.current = onChange;
  cursorRef.current = onCursorChange;

  useEffect(() => {
    if (!hostRef.current || editorRef.current) return undefined;
    const editor = monaco.editor.create(hostRef.current, {
      value: value || "", language: languageForPath(path), theme: "developer-os-dark",
      automaticLayout: true, minimap: { enabled: true, side: "right" },
      fontFamily: "JetBrains Mono, Consolas, Monaco, monospace", fontSize: 13, lineHeight: 21,
      tabSize: 2, insertSpaces: true, wordWrap: "off", smoothScrolling: true,
      cursorBlinking: "smooth", bracketPairColorization: { enabled: true },
      guides: { indentation: true, bracketPairs: true }, folding: true, stickyScroll: { enabled: true },
      renderWhitespace: "selection", scrollBeyondLastLine: false, padding: { top: 12, bottom: 24 },
      quickSuggestions: true, formatOnPaste: true, formatOnType: true, contextmenu: true,
      multiCursorModifier: "ctrlCmd", mouseWheelZoom: true, overviewRulerBorder: false,
      scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
    });
    editorRef.current = editor;
    modelRef.current = editor.getModel();
    const disposableChange = editor.onDidChangeModelContent(() => changeRef.current?.(editor.getValue()));
    const disposableCursor = editor.onDidChangeCursorPosition((event) => cursorRef.current?.({ line: event.position.lineNumber, column: event.position.column }));
    return () => { disposableChange.dispose(); disposableCursor.dispose(); editor.dispose(); editorRef.current = null; modelRef.current = null; };
  }, []);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const language = languageForPath(path);
    const nextUri = monaco.Uri.parse(`inmemory://developer-os/${encodeURIComponent(path || "untitled")}`);
    let model = monaco.editor.getModel(nextUri);
    if (!model) model = monaco.editor.createModel(value || "", language, nextUri);
    else { if (model.getValue() !== (value || "")) model.setValue(value || ""); monaco.editor.setModelLanguage(model, language); }
    if (editor.getModel() !== model) editor.setModel(model);
    modelRef.current = model;
  }, [path]);

  useEffect(() => {
    const model = modelRef.current;
    const next = value || "";
    if (model && model.getValue() !== next && !editorRef.current?.hasTextFocus()) model.setValue(next);
  }, [value]);

  return <div ref={hostRef} className="monaco-host" aria-label={`Developer OS code editor ${path || "untitled"}`} />;
}
