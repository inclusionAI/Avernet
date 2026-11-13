/** Pure versioned Actions. Business labels intentionally do not participate. */
import { DIAGNOSIS_V4_VERSION, type DiagnosisEvent, type Decision } from "./contracts.js";
import { TC_CODES } from "./tc-catalog.js";
export const ACTION_POLICY_VERSION = "diagnosis-actions/v1";
export const RECOVERY_POLICY_VERSION = "recovery-eligibility/v3";
export type Tags = { completed: boolean; correction: boolean; code: string | null };
export function deriveAlert(tags: Tags | null): Decision {
  return tags === null ? "UNRESOLVED" : !tags.completed && tags.code !== null ? "ALERT" : "PASS";
}
export function eligibleForRecovery(tags: Tags | null): boolean {
  return tags !== null && (!tags.completed || tags.code !== null);
}
/** Independent of allowlist/runtime readiness; caller has already validated the wire. */
export function recoverySkipReason(event: DiagnosisEvent): string | null {
  if (event.schemaVersion !== DIAGNOSIS_V4_VERSION) return "legacy_policy";
  if (event.decision === "UNRESOLVED") return "diagnosis_unresolved";
  if (!validActionEvent(event)) return "invalid_tags";
  return eligibleForRecovery({ completed: event.handlerName === "COMPLETED",
    correction: event.humanIntervention, code: event.tcFaultLabel }) ? null : "task_completed";
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
