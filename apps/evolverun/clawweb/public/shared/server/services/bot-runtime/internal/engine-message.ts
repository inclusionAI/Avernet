import type { ResolvedMessageInput, MessageReceipt, ShellResult } from "../contracts.js";
import { ENGINE_DELIVERY_SCRIPT } from "./engine-message-script.js";
const deliveryErrors: Record<string, string> = {
  SESSION_UNAVAILABLE: '目标会话不可用，消息未发送',
  ENGINE_CONNECTION_FAILED: '无法连接 Bot Engine，消息未发送',
  ENGINE_HANDSHAKE_FAILED: 'Bot Engine 未接受连接，消息未发送',
  ENGINE_REJECTED: 'Bot Engine 明确拒绝本次消息',
  ENGINE_ACK_UNKNOWN: 'Bot Engine 接收结果未知，请勿重复发送',
};
export async function sendEngineMessage(input: ResolvedMessageInput, options: {
  engineUrl: string; engineAuthToken: string;
  execute: (command: string) => Promise<ShellResult>;
}): Promise<MessageReceipt> {
  // Only the exact internal trial command may start a fresh OpenClaw session.
  // Ordinary session recovery still requires the original journal and identity.
  const trialId = /^agent:main:workflow-trial:([a-f0-9-]{36})$/.exec(input.sessionKey)?.[1];
  const createSession = input.target.activeEngine === 'openclaw' && !!trialId
    && input.deliveryKey === `workflow-trial:${trialId}` && input.message === `/workflow run request://${trialId} --remote`;
  const encoded = Buffer.from(JSON.stringify({ ...input, target: undefined,
    createSession,
    engine: input.target.activeEngine, engineUrl: options.engineUrl, engineAuthToken: options.engineAuthToken, program: ENGINE_DELIVERY_SCRIPT })).toString("base64");
  const script = Buffer.from(ENGINE_DELIVERY_SCRIPT).toString("base64");
  try {
    const result = await options.execute(`node -e 'eval(Buffer.from("${script}","base64").toString())' '${encoded}'`);
    if (result.status !== "success" || result.exitCode !== 0) return { status: "unknown", error: "Message worker outcome is unknown" };
    const value = JSON.parse(result.stdout.trim());
    if (!["accepted", "failed", "unknown"].includes(value.status)) throw Error("invalid receipt");
    if (value.status !== "accepted") return { status: value.status, error: Object.hasOwn(deliveryErrors, value.reasonCode)
      ? deliveryErrors[value.reasonCode] : "Bot 消息发送未获确认，请联系维护人员查看发送记录" };
    const location = ["confirmed", "unconfirmed", "ambiguous", "session_changed"].includes(value.locationStatus) ? value.locationStatus : "unconfirmed";
    const confirmed = location === "confirmed" && typeof value.messageId === "string" && !!value.messageId
      && typeof value.messageTimestamp === "string" && !!value.messageTimestamp;
    return { status: "accepted", locationStatus: location === "confirmed" && !confirmed ? "unconfirmed" : location,
      ...(typeof value.sessionId === "string" ? { sessionId: value.sessionId } : {}),
      ...(typeof value.runId === "string" ? { runId: value.runId } : {}),
      ...(Number.isSafeInteger(value.acceptedAt) ? { acceptedAt: value.acceptedAt } : {}),
      ...(confirmed ? { messageId: value.messageId, messageTimestamp: value.messageTimestamp,
        ...(Number.isSafeInteger(value.locatedAt) ? { locatedAt: value.locatedAt } : {}) } : {}) };
  } catch { return { status: "unknown", error: "Message outcome is unknown; not retried" }; }
}
