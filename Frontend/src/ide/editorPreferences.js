export const DEFAULT_EDITOR_PREFERENCES = Object.freeze({
  fontSize: 13,
  tabSize: 2,
  wordWrap: false,
  minimap: true,
  lineNumbers: true,
  stickyScroll: true,
  formatOnType: true,
  theme: "developer-os-dark",
});

const THEMES = new Set(["developer-os-dark", "vs-dark", "vs", "hc-black"]);

export function normalizeEditorPreferences(value) {
  const input = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const parsedFontSize = Number(input.fontSize);
  const fontSize = Number.isFinite(parsedFontSize)
    ? Math.min(24, Math.max(11, Math.round(parsedFontSize)))
    : DEFAULT_EDITOR_PREFERENCES.fontSize;

  return {
    fontSize,
    tabSize: Number(input.tabSize) === 4 ? 4 : 2,
    wordWrap: input.wordWrap === true,
    minimap: input.minimap !== false,
    lineNumbers: input.lineNumbers !== false,
    stickyScroll: input.stickyScroll !== false,
    formatOnType: input.formatOnType !== false,
    theme: THEMES.has(input.theme) ? input.theme : DEFAULT_EDITOR_PREFERENCES.theme,
  };
}
