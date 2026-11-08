/** Pure versioned Actions. Business labels intentionally do not participate. */
import { DIAGNOSIS_V4_VERSION, type DiagnosisEvent, type Decision } from "./contracts.js";
import { TC_CODES } from "./tc-catalog.js";
export const ACTION_POLICY_VERSION = "diagnosis-actions/v1";
export type Tags = { completed: boolean; correction: boolean; code: string | null };
export function deriveAlert(tags: Tags | null): Decision {
  return tags === null ? "UNRESOLVED" : !tags.completed && tags.code !== null ? "ALERT" : "PASS";
}
export function eligibleForRecovery(tags: Tags | null): boolean {
  return tags !== null && !tags.completed && !tags.correction && tags.code !== null;
}
/** Independent of allowlist/runtime readiness; caller has already validated the wire. */
export function recoverySkipReason(event: DiagnosisEvent): string | null {
  if (event.schemaVersion !== DIAGNOSIS_V4_VERSION) return "legacy_policy";
  if (event.decision === "UNRESOLVED") return "diagnosis_unresolved";
  if (event.handlerName === "COMPLETED") return "task_completed";
  if (!event.tcFaultLabel || !TC_CODES.has(event.tcFaultLabel)) return "no_tc_code";
  if (event.humanIntervention) return "human_correction";
  if (event.handlerName !== "NOT_COMPLETED" || event.decision !== "ALERT") return "invalid_tags";
  return null;
}
/** Fail closed on invalid v4 tags; legacy parsing stays in its original branch. */
export function validActionEvent(event: DiagnosisEvent): boolean {
  if (event.decision === "UNRESOLVED") return event.handlerName === null && !event.humanIntervention
    && event.tcFaultLabel === null && event.confidence === null
    && event.businessProblemCategory === null && event.businessProblemSubtype === null;
  if (event.handlerName !== "COMPLETED" && event.handlerName !== "NOT_COMPLETED") return false;
  const hasCode = event.tcFaultLabel !== null;
  if (hasCode && !TC_CODES.has(event.tcFaultLabel!)) return false;
  if (hasCode ? event.confidence === null : event.confidence !== null) return false;
  return deriveAlert({ completed: event.handlerName === "COMPLETED", correction: event.humanIntervention,
    code: event.tcFaultLabel }) === event.decision;
}
