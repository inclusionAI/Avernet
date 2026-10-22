import { MonitoringError, type MonitoringRecoveryResult } from "./contracts.js";

function requireValue(condition: unknown): asserts condition {
  if (!condition) throw new MonitoringError("INVALID_RESULT", "自愈回传结果格式无效。");
}
function object(value: unknown): Record<string, unknown> {
  requireValue(value !== null && typeof value === "object" && !Array.isArray(value));
  return value as Record<string, unknown>;
}
function keys(value: Record<string, unknown>, required: string[], optional: string[] = []) {
  requireValue(required.every(key => Object.hasOwn(value, key)));
  requireValue(Object.keys(value).every(key => required.includes(key) || optional.includes(key)));
}
function text(value: unknown): value is string { return typeof value === "string"; }
function nullableText(value: unknown) { return value === null || text(value); }
function integer(value: unknown, minimum = 0) {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum;
}

/** No evidence truncation or receiver-only item limits. The HTTP adapter bounds total bytes. */
export function parseRecoveryResult(input: unknown): MonitoringRecoveryResult {
  const result = object(input);
  keys(result, ["event_id", "task_id", "result_version", "status", "phase", "waiting_reason",
    "effect", "result_text", "evidence_ids"], ["delivery", "evidence", "error", "intervention_status", "completion_reason"]);
  requireValue(text(result.event_id) && /^[A-Za-z0-9_.:-]{1,128}$/.test(result.event_id));
  requireValue(text(result.task_id) && result.task_id.trim().length > 0);
  requireValue(integer(result.result_version, 1));
  requireValue(["waiting_effect", "completed", "failed", "canceled"].includes(result.status as string) && text(result.status));
  requireValue(["assess", "observe", "plan", "apply", "verify"].includes(result.phase as string) && text(result.phase));
  requireValue(result.waiting_reason === (result.status === "waiting_effect" ? "effect" : null));
  requireValue(["recovered", "not_recovered", "unknown", "not_intervened"].includes(result.effect as string) && text(result.effect));
  if (Object.hasOwn(result, "intervention_status")) requireValue(text(result.intervention_status) && ["none", "performed", "unknown"].includes(result.intervention_status));
  if (Object.hasOwn(result, "completion_reason")) requireValue(result.completion_reason === "user_continued" && result.status === "completed");
  requireValue(text(result.result_text));
  requireValue(Array.isArray(result.evidence_ids) && result.evidence_ids.every(text));
  if (Object.hasOwn(result, "error")) requireValue(nullableText(result.error));
  if (Object.hasOwn(result, "evidence")) {
    requireValue(Array.isArray(result.evidence));
    for (const raw of result.evidence) {
      const evidence = object(raw);
      keys(evidence, ["evidence_id", "tool_call_id", "source", "location", "excerpt"]);
      requireValue(Object.values(evidence).every(text));
    }
  }
  if (Object.hasOwn(result, "delivery")) {
    const delivery = object(result.delivery);
    keys(delivery, ["delivery_key", "status", "result_text", "started_at_ms", "finished_at_ms", "error"], ["result_payload"]);
    requireValue(text(delivery.delivery_key) && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(delivery.delivery_key));
    requireValue(["accepted", "failed", "unknown"].includes(delivery.status as string) && text(delivery.status));
    requireValue(text(delivery.result_text) && nullableText(delivery.error));
    requireValue(integer(delivery.started_at_ms) && integer(delivery.finished_at_ms));
    if (Object.hasOwn(delivery, "result_payload")) {
      requireValue(Object.values(object(delivery.result_payload)).every(value =>
        text(value) || (typeof value === "number" && Number.isFinite(value))));
    }
  }
  // Keep every allowed optional field; own a JSON snapshot, not the caller's mutable object.
  return JSON.parse(JSON.stringify(result)) as MonitoringRecoveryResult;
}
