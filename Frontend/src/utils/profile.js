/**
 * Validate profile links before persisting or rendering them.
 * Only absolute HTTP(S) URLs are allowed; never render executable or other schemes.
 */
export function normalizeProfileLink(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error("Profile links must be complete URLs beginning with https:// or http://.");
  }
  if (!["https:", "http:"].includes(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password) {
    throw new Error("Profile links must use HTTP or HTTPS and cannot contain embedded credentials.");
  }
  return parsed.toString();
}

export function safeProfileLink(value) {
  try {
    return normalizeProfileLink(value);
  } catch {
    return "";
  }
}
