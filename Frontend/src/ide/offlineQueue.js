export const OFFLINE_QUEUE_KEY = "dos-offline-queue";

export function parseOfflineQueue(raw) {
  try {
    const value = JSON.parse(raw || "[]");
    if (!Array.isArray(value)) return [];
    // Keep valid legacy entries even when they have no workspace_id. They cannot
    // be synced safely, but must not be silently discarded during another edit.
    return value.filter((item) =>
      item &&
      typeof item === "object" &&
      typeof item.path === "string" &&
      typeof item.content === "string"
    );
  } catch {
    return [];
  }
}

export function queueWorkspacePatch(queue, patch) {
  if (
    !patch ||
    patch.workspace_id == null ||
    String(patch.workspace_id).length === 0 ||
    typeof patch.path !== "string" ||
    typeof patch.content !== "string"
  ) {
    return Array.isArray(queue) ? queue : [];
  }

  return [
    ...(Array.isArray(queue) ? queue : []).filter((item) =>
      !(
        item &&
        item.workspace_id != null &&
        String(item.workspace_id) === String(patch.workspace_id) &&
        item.path === patch.path
      )
    ),
    patch,
  ];
}

export function getWorkspacePatches(queue, workspaceId) {
  if (workspaceId == null) return [];
  return (Array.isArray(queue) ? queue : []).filter((item) =>
    item &&
    item.workspace_id != null &&
    String(item.workspace_id) === String(workspaceId) &&
    typeof item.path === "string" &&
    typeof item.content === "string"
  );
}

export function removeWorkspacePatches(queue, workspaceId) {
  if (workspaceId == null) return Array.isArray(queue) ? queue : [];
  return (Array.isArray(queue) ? queue : []).filter((item) =>
    !item ||
    item.workspace_id == null ||
    String(item.workspace_id) !== String(workspaceId)
  );
}


// Offline edits do not include a full base snapshot for each file. Only replay them
// when the server is still at the exact revision they were based on; otherwise a
// "latest revision" retry could silently overwrite another collaborator's changes.
function parseWorkspaceRevision(value) {
  if (
    typeof value !== "number" &&
    !(typeof value === "string" && /^\d+$/.test(value.trim()))
  ) {
    return null;
  }
  const revision = Number(value);
  return Number.isSafeInteger(revision) && revision >= 0 ? revision : null;
}

export function canSafelySyncOfflinePatches(patches, serverRevision) {
  if (!Array.isArray(patches) || patches.length === 0) return false;

  // Missing server metadata must never coerce to revision 0. Offline replay
  // is destructive if it proceeds without an explicit, comparable revision.
  const currentRevision = parseWorkspaceRevision(serverRevision);
  if (currentRevision === null) return false;

  const revisions = patches.map((patch) => parseWorkspaceRevision(patch?.base_revision));
  if (revisions.some((revision) => revision === null)) return false;

  const uniqueRevisions = new Set(revisions);
  return uniqueRevisions.size === 1 && currentRevision === revisions[0];
}


// Remove only the exact patches that were successfully replayed. Newer edits
// queued while the network requests were in flight must remain recoverable.
export function removeSyncedWorkspacePatches(queue, syncedPatches) {
  const items = Array.isArray(queue) ? queue : [];
  const synced = Array.isArray(syncedPatches) ? syncedPatches : [];
  return items.filter((item) => {
    if (!item || item.workspace_id == null) return true;
    return !synced.some((patch) =>
      patch &&
      patch.workspace_id != null &&
      String(item.workspace_id) === String(patch.workspace_id) &&
      item.path === patch.path &&
      item.content === patch.content
    );
  });
}


// After a queued patch is confirmed saved, advance the base revision of remaining
// patches in that same workspace to the exact revision returned by that save.
// The next write still uses optimistic concurrency, so a later external write
// causes a conflict instead of being overwritten.
export function rebaseWorkspacePatches(queue, workspaceId, revision) {
  const parsedRevision = parseWorkspaceRevision(revision);
  if (workspaceId == null || parsedRevision === null) {
    return Array.isArray(queue) ? queue : [];
  }
  return (Array.isArray(queue) ? queue : []).map((item) =>
    item && item.workspace_id != null &&
    String(item.workspace_id) === String(workspaceId)
      ? { ...item, base_revision: parsedRevision }
      : item
  );
}
