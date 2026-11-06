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

function parseAckRevision(value) {
  if (typeof value !== "number" && !(typeof value === "string" && /^\d+$/.test(value.trim()))) return null;
  const revision = Number(value);
  return Number.isSafeInteger(revision) && revision >= 0 ? revision : null;
}

/** Reject delayed acknowledgements that could release a newer identical-content patch. */
export function isExpectedWorkspacePatchAck({
  activeWorkspaceId, inFlightWorkspaceId, ackWorkspaceId,
  inFlightPath, ackPath, inFlightContent, ackContent, baseRevision, ackRevision,
}) {
  const base = parseAckRevision(baseRevision);
  const acknowledged = parseAckRevision(ackRevision);
  return activeWorkspaceId != null &&
    inFlightWorkspaceId != null &&
    ackWorkspaceId != null &&
    String(activeWorkspaceId) === String(inFlightWorkspaceId) &&
    String(inFlightWorkspaceId) === String(ackWorkspaceId) &&
    typeof inFlightPath === "string" && inFlightPath.length > 0 &&
    inFlightPath === ackPath &&
    typeof inFlightContent === "string" && inFlightContent === ackContent &&
    base !== null && acknowledged !== null &&
    acknowledged === base + 1;
}
