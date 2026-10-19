export const DEFAULT_TERMINAL_BUFFER_LIMIT = 250_000;

export function appendBoundedText(previous, chunk, limit = DEFAULT_TERMINAL_BUFFER_LIMIT) {
  const max = Number.isFinite(limit) ? Math.max(1, Math.floor(limit)) : DEFAULT_TERMINAL_BUFFER_LIMIT;
  const next = String(previous ?? "") + String(chunk ?? "");
  return next.length > max ? next.slice(-max) : next;
}
