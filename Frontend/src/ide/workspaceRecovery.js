export const WORKSPACE_RECOVERY_KEY = "dos-workspace-recovery-v1";
export const WORKSPACE_RECOVERY_VERSION = 1;
export const MAX_RECOVERY_ENTRIES = 50;
export const MAX_RECOVERY_STORAGE_CHARS = 1_500_000;

function validRevision(value) {
  if (typeof value !== "number" && !(typeof value === "string" && /^\\d+$/.test(value.trim()))) return null;
  const revision = Number(value);
  return Number.isSafeInteger(revision) && revision >= 0 ? revision : null;
}

function isSafeWorkspacePath(path) {\n  if (typeof path !== "string" || !path || path.length > 512 || path.startsWith("/") || path.includes("\\\\") || path.includes("\\0")) return false;\n  const segments = path.split("/");\n  return segments.every((segment) => segment.length > 0 && segment !== "." && segment !== "..");\n}\n\nfunction validEntry(entry) {
  return Boolean(
    entry &&
    typeof entry === "object" &&
    entry.workspace_id != null &&
    String(entry.workspace_id).trim() !== "" &&
    typeof entry.path === "string" &&
    entry.path.length > 0 &&
    entry.path.length <= 512 &&
    typeof entry.content === "string" &&
    entry.content.length <= 750_000 &&
    (entry.base_content === null || typeof entry.base_content === "string") &&
    validRevision(entry.base_revision) !== null &&
    typeof entry.updated_at === "number" &&
    Number.isFinite(entry.updated_at) &&
    entry.updated_at >= 0
  );
}

/**
 * Parse a versioned, bounded local recovery journal. Corrupt or unknown
 * versions are ignored rather than interpreted as trusted recovery data.
 */
export function parseWorkspaceRecoveryJournal(raw) {
  try {
    const value = JSON.parse(raw || "null");
    if (!value || value.version !== WORKSPACE_RECOVERY_VERSION || !Array.isArray(value.entries)) {
      return { version: WORKSPACE_RECOVERY_VERSION, entries: [] };
    }
    const entries = value.entries.filter(validEntry).slice(-MAX_RECOVERY_ENTRIES);
    return { version: WORKSPACE_RECOVERY_VERSION, entries };
  } catch {
    return { version: WORKSPACE_RECOVERY_VERSION, entries: [] };
  }
}

export function getWorkspaceRecoverySnapshots(journal, workspaceId) {
  if (workspaceId == null || String(workspaceId).trim() === "") return [];
  const entries = Array.isArray(journal?.entries) ? journal.entries : [];
  return entries
    .filter((entry) => validEntry(entry) && String(entry.workspace_id) === String(workspaceId))
    .sort((a, b) => a.updated_at - b.updated_at);
}

/**
 * Add or replace one workspace/path snapshot. Oldest entries are evicted first
 * to keep localStorage bounded; an oversized single snapshot is rejected.
 */
export function upsertWorkspaceRecoverySnapshot(journal, snapshot) {
  const current = {
    version: WORKSPACE_RECOVERY_VERSION,
    entries: Array.isArray(journal?.entries) ? journal.entries.filter(validEntry) : [],
  };
  const entry = {
    ...snapshot,
    workspace_id: snapshot?.workspace_id == null ? "" : String(snapshot.workspace_id),
    base_revision: validRevision(snapshot?.base_revision),
    updated_at: Number.isFinite(snapshot?.updated_at) ? snapshot.updated_at : Date.now(),
  };
  if (!validEntry(entry)) return current;

  let entries = current.entries.filter((item) =>
    !(String(item.workspace_id) === entry.workspace_id && item.path === entry.path)
  );
  entries.push(entry);
  entries.sort((a, b) => a.updated_at - b.updated_at);
  while (
    entries.length > MAX_RECOVERY_ENTRIES ||
    JSON.stringify({ version: WORKSPACE_RECOVERY_VERSION, entries }).length > MAX_RECOVERY_STORAGE_CHARS
  ) {
    if (entries.length <= 1) return current;
    entries.shift();
  }
  return { version: WORKSPACE_RECOVERY_VERSION, entries };
}

export function removeWorkspaceRecoverySnapshot(journal, workspaceId, path) {
  if (workspaceId == null || path == null) {
    return {
      version: WORKSPACE_RECOVERY_VERSION,
      entries: Array.isArray(journal?.entries) ? journal.entries.filter(validEntry) : [],
    };
  }
  return {
    version: WORKSPACE_RECOVERY_VERSION,
    entries: (Array.isArray(journal?.entries) ? journal.entries : []).filter((entry) =>
      validEntry(entry) &&
      !(String(entry.workspace_id) === String(workspaceId) && entry.path === path)
    ),
  };
}
