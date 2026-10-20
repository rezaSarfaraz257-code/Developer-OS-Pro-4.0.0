export function canAcknowledgeWorkspaceEdit({
  activeWorkspaceId,
  ackWorkspaceId,
  ackPath,
  currentContent,
  ackContent,
  hasPendingEdit,
  saving,
}) {
  return (
    activeWorkspaceId != null &&
    ackWorkspaceId != null &&
    String(activeWorkspaceId) === String(ackWorkspaceId) &&
    typeof ackPath === "string" &&
    ackPath.length > 0 &&
    typeof ackContent === "string" &&
    currentContent === ackContent &&
    hasPendingEdit !== true &&
    saving !== true
  );
}
