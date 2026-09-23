import type { ResolvedMessageInput, MessageReceipt, ShellResult } from "../contracts.js";
import { ENGINE_DELIVERY_SCRIPT } from "./engine-message-script.js";
export async function sendEngineMessage(input: ResolvedMessageInput, options: {
  engineUrl: string; engineAuthToken: string;
  execute: (command: string) => Promise<ShellResult>;
}): Promise<MessageReceipt> {
  const encoded = Buffer.from(JSON.stringify({ ...input, target: undefined,
    engineUrl: options.engineUrl, engineAuthToken: options.engineAuthToken, program: ENGINE_DELIVERY_SCRIPT })).toString("base64");
  const script = Buffer.from(ENGINE_DELIVERY_SCRIPT).toString("base64");
  try {
    const result = await options.execute(`node -e 'eval(Buffer.from("${script}","base64").toString())' '${encoded}'`);
    if (result.status !== "success" || result.exitCode !== 0) return { status: "unknown", error: "Message worker outcome is unknown" };
    const value = JSON.parse(result.stdout.trim());
    if (!["accepted", "failed", "unknown"].includes(value.status)) throw Error("invalid receipt");
    if (value.status !== "accepted") return { status: value.status, error: "Message was not confirmed" };
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
