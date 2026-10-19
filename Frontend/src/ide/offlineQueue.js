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
