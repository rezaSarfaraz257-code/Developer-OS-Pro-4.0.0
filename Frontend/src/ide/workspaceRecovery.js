const DB_NAME = "developer-os-workspace-recovery";
const DB_VERSION = 1;
const STORE_NAME = "snapshots";
const LOCAL_STORAGE_PREFIX = "dos-workspace-recovery:";
const MAX_FILE_BYTES = 1_000_000;
const MAX_SNAPSHOT_BYTES = 50_000_000;
const MAX_FILES = 2_000;

function normalizeRevision(value) {
  if (typeof value !== "number" && !(typeof value === "string" && /^\\d+$/.test(value.trim()))) {
    return null;
  }
  const revision = Number(value);
  return Number.isSafeInteger(revision) && revision >= 0 ? revision : null;
}

function normalizePath(value) {
  if (typeof value !== "string" || !value || value.length > 500) return null;
  if (/[\\x00-\\x1f\\x7f]/.test(value)) return null;
  const normalized = value.replace(/\\\\/g, "/");
  if (normalized.startsWith("/") || /^[A-Za-z]:/.test(normalized)) return null;
  const parts = normalized.split("/");
  if (parts.some((part) => !part || part === "." || part === ".." || part.toLowerCase() === ".git" || part.includes(":"))) {
    return null;
  }
  return normalized;
}

export function normalizeWorkspaceRecoverySnapshot(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (value.workspace_id == null || String(value.workspace_id).trim() === "") return null;
  const base_revision = normalizeRevision(value.base_revision);
  if (base_revision === null) return null;
  if (!value.files || typeof value.files !== "object" || Array.isArray(value.files)) return null;
  const entries = Object.entries(value.files);
  if (!entries.length || entries.length > MAX_FILES) return null;

  const files = {};
  let totalBytes = 0;
  for (const [rawPath, content] of entries) {
    const path = normalizePath(rawPath);
    if (!path || typeof content !== "string" || content.includes("\\0")) return null;
    let bytes;
    try {
      bytes = new TextEncoder().encode(content).byteLength;
    } catch {
      return null;
    }
    if (bytes > MAX_FILE_BYTES) return null;
    totalBytes += bytes;
    if (totalBytes > MAX_SNAPSHOT_BYTES) return null;
    files[path] = content;
  }

  const active_file = value.active_file == null ? "" : normalizePath(value.active_file);
  if (value.active_file != null && !active_file) return null;
  const updated_at = Number.isSafeInteger(value.updated_at) && value.updated_at >= 0
    ? value.updated_at
    : Date.now();

  return {
    schema_version: 1,
    workspace_id: String(value.workspace_id),
    base_revision,
    active_file,
    updated_at,
    files,
  };
}

function openDatabase() {
  if (!globalThis.indexedDB) return Promise.reject(new Error("IndexedDB is unavailable."));
  return new Promise((resolve, reject) => {
    const request = globalThis.indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        database.createObjectStore(STORE_NAME, { keyPath: "workspace_id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Could not open workspace recovery storage."));
    request.onblocked = () => reject(new Error("Workspace recovery storage is blocked by another tab."));
  });
}

function transact(database, mode, operation) {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, mode);
    const store = transaction.objectStore(STORE_NAME);
    let request;
    try {
      request = operation(store);
    } catch (error) {
      reject(error);
      return;
    }
    transaction.oncomplete = () => resolve(request?.result);
    transaction.onerror = () => reject(transaction.error || new Error("Workspace recovery transaction failed."));
    transaction.onabort = () => reject(transaction.error || new Error("Workspace recovery transaction was aborted."));
  });
}

function localStorageKey(workspaceId) {
  return LOCAL_STORAGE_PREFIX + String(workspaceId);
}

function saveLocalFallback(snapshot) {
  const encoded = JSON.stringify(snapshot);
  // localStorage is only a bounded fallback. IndexedDB is the primary store
  // because a legitimate workspace snapshot can exceed browser key/value quotas.
  if (new TextEncoder().encode(encoded).byteLength > 4_000_000) {
    throw new Error("Workspace recovery snapshot exceeds the safe local fallback limit.");
  }
  if (!globalThis.localStorage) throw new Error("Browser recovery storage is unavailable.");
  globalThis.localStorage.setItem(localStorageKey(snapshot.workspace_id), encoded);
}

function loadLocalFallback(workspaceId) {
  if (!globalThis.localStorage) return null;
  const raw = globalThis.localStorage.getItem(localStorageKey(workspaceId));
  if (!raw) return null;
  try {
    const parsed = normalizeWorkspaceRecoverySnapshot(JSON.parse(raw));
    return parsed && parsed.workspace_id === String(workspaceId) ? parsed : null;
  } catch {
    return null;
  }
}

export async function saveWorkspaceRecoverySnapshot(value) {
  const snapshot = normalizeWorkspaceRecoverySnapshot(value);
  if (!snapshot) throw new Error("Invalid workspace recovery snapshot.");
  try {
    const database = await openDatabase();
    try {
      await transact(database, "readwrite", (store) => store.put(snapshot));
    } finally {
      database.close();
    }
    try {
      saveLocalFallback(snapshot);
    } catch {
      try { globalThis.localStorage?.removeItem(localStorageKey(snapshot.workspace_id)); } catch { /* IndexedDB remains authoritative. */ }
    }
    return { storage: "indexeddb", snapshot };
  } catch (indexedDbError) {
    try {
      saveLocalFallback(snapshot);
      return { storage: "localstorage", snapshot };
    } catch (fallbackError) {
      throw new Error("Could not persist workspace recovery data.", { cause: fallbackError || indexedDbError });
    }
  }
}

export async function loadWorkspaceRecoverySnapshot(workspaceId) {
  if (workspaceId == null || String(workspaceId).trim() === "") return null;
  try {
    const database = await openDatabase();
    try {
      const result = await transact(database, "readonly", (store) => store.get(String(workspaceId)));
      const normalized = normalizeWorkspaceRecoverySnapshot(result);
      if (normalized && normalized.workspace_id === String(workspaceId)) return normalized;
    } finally {
      database.close();
    }
  } catch {
    // The local fallback is intentionally checked if IndexedDB is unavailable.
  }
  return loadLocalFallback(workspaceId);
}

export async function deleteWorkspaceRecoverySnapshot(workspaceId) {
  if (workspaceId == null || String(workspaceId).trim() === "") return;
  let indexedDbError = null;
  try {
    const database = await openDatabase();
    try {
      await transact(database, "readwrite", (store) => store.delete(String(workspaceId)));
    } finally {
      database.close();
    }
  } catch (error) {
    indexedDbError = error;
  }
  try {
    if (!globalThis.localStorage) throw new Error("Local recovery storage is unavailable.");
    globalThis.localStorage.removeItem(localStorageKey(workspaceId));
  } catch (error) {
    if (indexedDbError) throw new Error("Could not clear workspace recovery data.", { cause: error });
  }
}
