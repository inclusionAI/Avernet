import { redactText } from "@avernet/clawweb-shared/server/services/redaction";
import type { DeliveryReceipt, RecoveryRequest, RecoveryResult } from "./contracts.js";

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
