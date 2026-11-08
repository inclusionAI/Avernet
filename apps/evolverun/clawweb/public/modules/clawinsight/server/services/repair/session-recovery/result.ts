import { redactText } from "@avernet/clawweb-shared/server/services/redaction";
import type { DeliveryReceipt, RecoveryRequest, RecoveryResult } from "./contracts.js";
import type { AgentToolCall } from "../../../repositories/repair/agent-tool-call-record.js";
import type { RecoveryInterventionStatus } from "./task-contracts.js";

/** Complete task audit, including prior executions. Acceptance alone does not confirm message presence. */
export function recoveryInterventionStatus(calls: readonly AgentToolCall[], delivery?: RecoveryResult["delivery"]): RecoveryInterventionStatus {
  const confirmedMessages = new Set(calls.filter(call => call.status === "succeeded" && !call.isWrite
    && ["session_read", "trajectory_read"].includes(call.toolName))
    .map(call => call.result as Record<string, unknown> | null)
    .filter(result => result?.delivery_confirmed === true && typeof result.delivery_key === "string")
    .map(result => result!.delivery_key));
  const reconciled = new Map(calls.filter(call => call.status === "succeeded" && call.toolName === "reconcile_write" && !call.isWrite)
    .map(call => call.result as Record<string, unknown> | null)
    .filter(result => typeof result?.sourceToolCallId === "string" && ["applied", "not_applied"].includes(String(result.outcome)))
    .map(result => [result!.sourceToolCallId, result!.outcome]));
  if (delivery && confirmedMessages.has(delivery.delivery_key)) return "performed";
  // A legacy delivery without its source audit is not evidence that nothing was sent.
  let uncertain = !!delivery && delivery.status !== "failed" && !calls.some(call => call.isWrite && call.toolName === "retry_message"
    && (call.result as Record<string, unknown> | null)?.delivery_key === delivery.delivery_key);
  for (const call of calls) {
    if (!call.isWrite) continue;
    const result = call.result as Record<string, unknown> | null;
    if (result?.dry_run === true || call.status === "pending" || result?.dispatched === false) continue;
    if (call.toolName === "apply_action" && call.status === "unknown" && reconciled.has(call.callId)) {
      if (reconciled.get(call.callId) === "applied") return "performed";
      continue;
    }
    if (call.toolName === "retry_message") {
      if (typeof result?.delivery_key === "string" && confirmedMessages.has(result.delivery_key)) return "performed";
      if (call.status === "failed" && result?.status === "failed") continue;
    } else if (call.toolName === "apply_action" && call.status === "succeeded" && result?.status === "success"
      && (result.exitCode === 0 || typeof result.httpStatus === "number" && result.httpStatus >= 200 && result.httpStatus < 300)) return "performed";
    // A failed command may have partly run; missing receipts and active calls remain uncertain.
    uncertain = true;
  }
  return uncertain ? "unknown" : "none";
}

/** Whitelist actual receipt metadata; never forward advice, diagnosis or arbitrary provider fields. */
export function recoveryResult(request: RecoveryRequest, receipt: DeliveryReceipt, startedAt: number, finishedAt: number): RecoveryResult {
  const payload: Record<string, string | number> = {};
  const fields = {
    platformMessageId: "provider_message_id", sessionId: "session_id", messageId: "message_id",
  } as const;
  for (const [source, destination] of Object.entries(fields)) {
    const value = receipt[source as keyof typeof fields];
    if (typeof value === "string" && value) payload[destination] = redactText(value, 2048);
  }
  return { event_id: request.event_id, delivery: {
    delivery_key: request.delivery_key, status: receipt.status,
    result_text: receipt.status === "accepted" ? "建议消息已被投递通道接受；尚未判断原任务是否恢复。" : "",
    result_payload: payload, started_at_ms: startedAt, finished_at_ms: finishedAt,
    error: receipt.status === "accepted" ? null : receipt.status === "failed" ? "发送通道未接收建议消息" : "发送结果未知；未自动重发",
  } };
}
