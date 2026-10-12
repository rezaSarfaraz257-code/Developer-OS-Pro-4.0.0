export const DEFAULT_EDITOR_PREFERENCES = Object.freeze({
  fontSize: 13,
  tabSize: 2,
  wordWrap: false,
  minimap: true,
  lineNumbers: true,
  stickyScroll: true,
  formatOnType: true,
  fontLigatures: false,
  smoothCaret: true,
  renderWhitespace: "selection",
  codeLens: true,
  theme: "developer-os-dark"
});
const THEMES = new Set(["developer-os-dark", "vs-dark", "vs", "hc-black"]);
const WHITESPACE_MODES = new Set(["none", "selection", "all"]);

export function normalizeEditorPreferences(value) {
  const input = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const parsed = Number(input.fontSize);
  const tabSize = Number(input.tabSize);
  return {
    fontSize: Number.isFinite(parsed) ? Math.min(24, Math.max(11, Math.round(parsed))) : DEFAULT_EDITOR_PREFERENCES.fontSize,
    tabSize: tabSize === 4 ? 4 : 2,
    wordWrap: input.wordWrap === true,
    minimap: input.minimap !== false,
    lineNumbers: input.lineNumbers !== false,
    stickyScroll: input.stickyScroll !== false,
    formatOnType: input.formatOnType !== false,
    fontLigatures: input.fontLigatures === true,
    smoothCaret: input.smoothCaret !== false,
    renderWhitespace: WHITESPACE_MODES.has(input.renderWhitespace) ? input.renderWhitespace : DEFAULT_EDITOR_PREFERENCES.renderWhitespace,
    codeLens: input.codeLens !== false,
    theme: THEMES.has(input.theme) ? input.theme : DEFAULT_EDITOR_PREFERENCES.theme
  };
}
