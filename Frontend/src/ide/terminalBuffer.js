export const DEFAULT_TERMINAL_BUFFER_LIMIT = 250_000;

export function appendBoundedText(previous, chunk, limit = DEFAULT_TERMINAL_BUFFER_LIMIT) {
  const max = Number.isFinite(limit) ? Math.max(1, Math.floor(limit)) : DEFAULT_TERMINAL_BUFFER_LIMIT;
  const incoming = String(chunk ?? "");
  if (incoming.length >= max) return incoming.slice(-max);

  const current = String(previous ?? "");
  const keep = max - incoming.length;
  return (current.length > keep ? current.slice(-keep) : current) + incoming;
}
