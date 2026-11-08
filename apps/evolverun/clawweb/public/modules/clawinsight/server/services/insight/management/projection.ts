import { managementEvents, serializeManagementEvent } from "./contracts.js";

/** Management is not repair verification. Later actual verification wins. */
export function managementProjection(text: string | null) {
  const source = text ?? "";
  const events = managementEvents(text);
  const stateEvent = [...events].reverse().find(event => event.action !== "edit");
  const edit = [...events].reverse().find(event => event.suggestedAction !== undefined);
  const stateIndex = stateEvent ? source.lastIndexOf(`[改进项管理]\n记录：${serializeManagementEvent(stateEvent)}`) : -1;
  const verificationIndex = Math.max(source.lastIndexOf("[自动验证]"), source.lastIndexOf("[强制验收]"));
  const handledIndex = Math.max(source.lastIndexOf("[用户已处理]"), source.lastIndexOf("[管理员已处理]"));
  const rejectionIndex = Math.max(source.lastIndexOf("[用户驳回]"), source.lastIndexOf("[管理员驳回]"), source.lastIndexOf("[Admin驳回]"));
  return {
    ...(edit ? { suggestedAction: edit.suggestedAction || null } : {}),
    ...(stateEvent && stateIndex > verificationIndex ? {
      verificationStatus: "INSUFFICIENT_DATA", verificationLastCheckedAt: null,
      verificationNewSessionCount: 0, verificationLastRecurrenceAt: null,
      resolvedSource: stateEvent.action === "close" ? "MANUAL_CLOSED" : null,
    } : {}),
    ...(stateEvent && stateIndex > handledIndex ? { handledAt: null } : {}),
    ...(stateEvent && stateIndex > rejectionIndex ? {
      rejectedBy: stateEvent.action === "reject" ? "ADMIN" as const : null,
      rejectReasonCode: stateEvent.action === "reject" ? "OTHER" : null,
      rejectComment: stateEvent.action === "reject" ? stateEvent.reason : null,
      rejectedAt: stateEvent.action === "reject" ? stateEvent.at : null,
    } : {}),
  };
}
