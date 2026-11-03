import { Buffer } from "node:buffer";

export const RUN_LOG_MESSAGE_MAX_BYTES = 65_535;
export const RUN_LOG_MESSAGE_TRUNCATION_MARKER = "\n...[truncated]";

const contentByteBudget = RUN_LOG_MESSAGE_MAX_BYTES -
  Buffer.byteLength(RUN_LOG_MESSAGE_TRUNCATION_MARKER, "utf8");

export function truncateRunLogMessage(message: string): string {
  if (Buffer.byteLength(message, "utf8") <= RUN_LOG_MESSAGE_MAX_BYTES) return message;

  let bytes = 0;
  let end = 0;
  for (const codepoint of message) {
    const width = Buffer.byteLength(codepoint, "utf8");
    if (bytes + width > contentByteBudget) break;
    bytes += width;
    end += codepoint.length;
  }
  return message.slice(0, end) + RUN_LOG_MESSAGE_TRUNCATION_MARKER;
}
