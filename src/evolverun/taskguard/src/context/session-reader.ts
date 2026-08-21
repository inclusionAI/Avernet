/**
 * Session file reader/writer for JSONL session files used by embedded agents.
 *
 * Parses the JSONL format written by `buildNodeExecutionContext` and appended to
 * by the pi-agent runtime. Provides structured access to messages, token estimation,
 * and safe read/write operations.
 *
 * @module context/session-reader
 */

import { readFile, writeFile, mkdir, rename } from "node:fs/promises";
import { join, dirname } from "node:path";
import { estimateTextTokens } from "./token-counter.js";

// ── Content Block Types ──

/** A text content block in a message. */
export type TextBlock = {
  type: "text";
  text: string;
};

/** A tool use content block (assistant calling a tool). */
export type ToolUseBlock = {
  type: "tool_use" | "toolCall";
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
};

/** A tool result content block (user providing tool output). */
export type ToolResultBlock = {
  type: "tool_result";
  tool_use_id?: string;
  content?: string | ContentBlock[];
  is_error?: boolean;
};

/** Any content block in a message. */
export type ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | Record<string, unknown>;

// ── Session Message ──

/** A parsed message from a JSONL session file. */
export type SessionMessage = {
  /** The role of the message author. */
  role: "system" | "user" | "assistant";
  /** The raw line from the JSONL file (for preservation). */
  raw: string;
  /** Text content extracted from the message (for token estimation and compression). */
  text: string;
  /** Whether this message contains a tool_result block. */
  isToolResult: boolean;
  /** Whether this message contains a tool_use/toolCall block. */
  isToolUse: boolean;
  /** Whether this message indicates an error. */
  isError: boolean;
  /** The tool name if this is a tool_use or tool_result message. */
  toolName: string | undefined;
  /** Estimated token count for this message. */
  tokenCount: number;
  /** The parsed content blocks (if available). */
  contentBlocks: ContentBlock[];
  /** The original timestamp from the JSONL line (if present). */
  timestamp: string | undefined;
};

/** A parsed session with aggregate statistics. */
export type ParsedSession = {
  /** All messages in order. */
  messages: SessionMessage[];
  /** Total estimated token count across all messages. */
  totalTokens: number;
  /** Number of system messages. */
  systemMessageCount: number;
  /** Number of tool_result messages. */
  toolResultCount: number;
  /** Number of tool_use messages. */
  toolUseCount: number;
  /** Number of assistant messages. */
  assistantMessageCount: number;
  /** Number of user messages (excluding tool_result). */
  userMessageCount: number;
};

// ── Raw Line Preservation (for safe sidecar writes) ──

/**
 * A raw line from the JSONL file, classified as either a parseable message
 * or a non-message structural entry.
 *
 * The OpenClaw transcript JSONL mixes message lines (system/user/assistant,
 * wrapped in `{ type: "message", message: { role, content } }`) with structural
 * entries that `parseSessionLine` intentionally drops:
 *   - `type:"session"` header
 *   - `model_change`
 *   - `thinking_level_change`
 *   - `custom`
 *   - `type:"compaction"` durable summary (OpenClaw core recovery checkpoint)
 *
 * Writing a compressed session back with only `messages.map(msg.raw)` discards
 * all structural entries, which caused the in-place-rewrite data-loss bug.
 * `RawSessionLine` preserves every original line verbatim so a sidecar cache
 * can be written without losing transcript structure.
 */
export type RawSessionLine =
  | {
    /** This line is a parseable session message. */
    kind: "message";
    /** The original JSONL line, byte-for-byte. */
    raw: string;
    /** The structured message parsed from `raw`. */
    message: SessionMessage;
  }
  | {
    /** This line is NOT a parseable message (session header, model_change, etc.). */
    kind: "non_message";
    /** The original JSONL line, byte-for-byte. */
    raw: string;
    /** The `type` field extracted from the JSON line, for diagnostics only. */
    type: string | undefined;
  };

/** Result of reading a session file with full line preservation. */
export type RawSessionFile = {
  /** All lines in file order, each classified as message or non_message. */
  rawLines: RawSessionLine[];
  /** The subset of `rawLines` that are messages, in order, with tool names resolved. */
  messages: SessionMessage[];
  /** Total estimated token count across all messages. */
  totalTokens: number;
  /** File not found / unreadable. */
  notFound: boolean;
};

// ── JSONL Parsing ──

/**
 * Parse a single JSONL line into a SessionMessage.
 * Returns null for empty or unparseable lines.
 */
export function parseSessionLine(line: string): SessionMessage | null {
  const trimmed = line.trim();
  if (!trimmed) return null;

  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return null;
  }

  // Unwrap nested envelope: { type: "message", message: { role, content } }
  const message = typeof parsed.message === "object" && parsed.message !== null
    ? parsed.message as Record<string, unknown>
    : parsed;

  const role = typeof message.role === "string" ? message.role : undefined;
  if (!role || !["system", "user", "assistant"].includes(role)) return null;

  const content = message.content;
  const contentBlocks = parseContentBlocks(content);
  const text = extractTextFromBlocks(contentBlocks);
  const toolName = extractToolName(contentBlocks, role);
  const isToolResult = contentBlocks.some(
    (b) => (b as Record<string, unknown>).type === "tool_result",
  );
  const isToolUse = contentBlocks.some(
    (b) => (b as Record<string, unknown>).type === "tool_use"
      || (b as Record<string, unknown>).type === "toolCall",
  );
  const isError = isToolResult && contentBlocks.some(
    (b) => (b as Record<string, unknown>).is_error === true,
  );

  const timestamp = typeof parsed.timestamp === "string"
    ? parsed.timestamp
    : typeof message.timestamp === "string"
      ? message.timestamp
      : undefined;

  return {
    role: role as "system" | "user" | "assistant",
    raw: trimmed,
    text,
    isToolResult,
    isToolUse,
    isError,
    toolName,
    tokenCount: estimateTextTokens(text) + 4, // +4 per-message overhead
    contentBlocks,
    timestamp,
  };
}

/**
 * Parse content blocks from a message's content field.
 * Content can be a string or an array of content blocks.
 */
function parseContentBlocks(content: unknown): ContentBlock[] {
  if (typeof content === "string") {
    return [{ type: "text", text: content }];
  }

  if (Array.isArray(content)) {
    return content.filter(
      (block): block is Record<string, unknown> =>
        typeof block === "object" && block !== null,
    );
  }

  return [];
}

/**
 * Extract concatenated text from content blocks.
 */
function extractTextFromBlocks(blocks: ContentBlock[]): string {
  const parts: string[] = [];

  for (const block of blocks) {
    const type = (block as Record<string, unknown>).type;

    if (type === "text" && typeof (block as TextBlock).text === "string") {
      parts.push((block as TextBlock).text);
    } else if (type === "tool_result") {
      const resultBlock = block as ToolResultBlock;
      if (typeof resultBlock.content === "string") {
        parts.push(resultBlock.content);
      } else if (Array.isArray(resultBlock.content)) {
        for (const sub of resultBlock.content) {
          if (typeof sub === "object" && sub !== null && (sub as TextBlock).type === "text") {
            parts.push((sub as TextBlock).text);
          }
        }
      }
    } else if (type === "tool_use" || type === "toolCall") {
      const toolBlock = block as ToolUseBlock;
      const name = toolBlock.name ?? "unknown";
      const input = toolBlock.input;
      if (input && typeof input === "object") {
        parts.push(`[${name}(${summarizeToolInput(input)})]`);
      } else {
        parts.push(`[${name}]`);
      }
    }
  }

  return parts.join("\n");
}

/**
 * Extract tool name from content blocks.
 */
function extractToolName(blocks: ContentBlock[], role: string): string | undefined {
  for (const block of blocks) {
    const type = (block as Record<string, unknown>).type;
    if ((type === "tool_use" || type === "toolCall") && typeof (block as ToolUseBlock).name === "string") {
      return (block as ToolUseBlock).name;
    }
    if (type === "tool_result") {
      // For tool_result, try to find the corresponding tool name from the preceding tool_use
      // This is a best-effort heuristic
      continue;
    }
  }
  return undefined;
}

/**
 * Create a brief summary of a tool input object for token counting.
 */
function summarizeToolInput(input: Record<string, unknown>): string {
  const keys = Object.keys(input);
  if (keys.length <= 3) {
    return keys.map((k) => `${k}=${truncate(String(input[k]), 50)}`).join(", ");
  }
  return `${keys.slice(0, 3).join(", ")}, ... (${keys.length} keys)`;
}

/**
 * Truncate a string to a maximum length.
 */
function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

// ── File I/O ──

/**
 * Read and parse a JSONL session file into structured messages.
 */
export async function readSessionFile(filePath: string): Promise<ParsedSession> {
  let content: string;
  try {
    content = await readFile(filePath, "utf8");
  } catch {
    return {
      messages: [],
      totalTokens: 0,
      systemMessageCount: 0,
      toolResultCount: 0,
      toolUseCount: 0,
      assistantMessageCount: 0,
      userMessageCount: 0,
    };
  }

  const lines = content.split(/\r?\n/).filter((line) => line.trim().length > 0);
  const messages: SessionMessage[] = [];

  for (const line of lines) {
    const msg = parseSessionLine(line);
    if (msg) messages.push(msg);
  }

  // Resolve tool names for tool_result messages by matching tool_use_id
  const resolvedMessages = resolveToolResultNames(messages);

  let totalTokens = 0;
  let systemMessageCount = 0;
  let toolResultCount = 0;
  let toolUseCount = 0;
  let assistantMessageCount = 0;
  let userMessageCount = 0;

  for (const msg of resolvedMessages) {
    totalTokens += msg.tokenCount;
    switch (msg.role) {
      case "system": systemMessageCount++; break;
      case "assistant": assistantMessageCount++; break;
      case "user":
        if (msg.isToolResult) toolResultCount++;
        else userMessageCount++;
        break;
    }
    if (msg.isToolUse) toolUseCount++;
  }

  return {
    messages: resolvedMessages,
    totalTokens,
    systemMessageCount,
    toolResultCount,
    toolUseCount,
    assistantMessageCount,
    userMessageCount,
  };
}

/**
 * Synchronous version of readSessionFile for use in synchronous contexts.
 */
export function readSessionFileSync(
  filePath: string,
  readSync: (path: string) => string,
): ParsedSession {
  let content: string;
  try {
    content = readSync(filePath);
  } catch {
    return {
      messages: [],
      totalTokens: 0,
      systemMessageCount: 0,
      toolResultCount: 0,
      toolUseCount: 0,
      assistantMessageCount: 0,
      userMessageCount: 0,
    };
  }

  const lines = content.split(/\r?\n/).filter((line) => line.trim().length > 0);
  const messages: SessionMessage[] = [];

  for (const line of lines) {
    const msg = parseSessionLine(line);
    if (msg) messages.push(msg);
  }

  // Resolve tool names for tool_result messages
  const resolvedMessages = resolveToolResultNames(messages);

  let totalTokens = 0;
  let systemMessageCount = 0;
  let toolResultCount = 0;
  let toolUseCount = 0;
  let assistantMessageCount = 0;
  let userMessageCount = 0;

  for (const msg of resolvedMessages) {
    totalTokens += msg.tokenCount;
    switch (msg.role) {
      case "system": systemMessageCount++; break;
      case "assistant": assistantMessageCount++; break;
      case "user":
        if (msg.isToolResult) toolResultCount++;
        else userMessageCount++;
        break;
    }
    if (msg.isToolUse) toolUseCount++;
  }

  return {
    messages: resolvedMessages,
    totalTokens,
    systemMessageCount,
    toolResultCount,
    toolUseCount,
    assistantMessageCount,
    userMessageCount,
  };
}

/**
 * Write a list of SessionMessage objects back to a JSONL file.
 * Uses the original raw lines to preserve formatting, but supports
 * replacing messages with modified versions.
 */
export async function writeSessionFile(
  filePath: string,
  messages: SessionMessage[],
): Promise<void> {
  const dir = dirname(filePath);
  await mkdir(dir, { recursive: true });

  const lines = messages.map((msg) => msg.raw);
  await writeFile(filePath, `${lines.join("\n")}\n`, "utf8");
}

/**
 * Read a JSONL session file preserving EVERY line (both messages and
 * non-message structural entries).
 *
 * Unlike {@link readSessionFile}, this never discards structural entries
 * (session header, model_change, etc.), which is essential for safely writing
 * a compressed sidecar cache that still contains the OpenClaw transcript
 * structure. The `messages` field is the same resolved-message subset that
 * `readSessionFile` produces, so compression logic can operate on it
 * unchanged.
 */
export async function readSessionFileRaw(
  filePath: string,
): Promise<RawSessionFile> {
  let content: string;
  try {
    content = await readFile(filePath, "utf8");
  } catch {
    return {
      rawLines: [],
      messages: [],
      totalTokens: 0,
      notFound: true,
    };
  }

  const lines = content.split(/\r?\n/).filter((line) => line.trim().length > 0);
  const rawLines: RawSessionLine[] = [];
  const messages: SessionMessage[] = [];

  for (const line of lines) {
    const msg = parseSessionLine(line);
    if (msg) {
      rawLines.push({ kind: "message", raw: msg.raw, message: msg });
      messages.push(msg);
    } else {
      const trimmed = line.trim();
      const type = extractLineType(trimmed);
      rawLines.push({ kind: "non_message", raw: trimmed, type });
    }
  }

  // Resolve tool names for tool_result messages by matching tool_use_id
  // (mirrors readSessionFile). Tool name enrichment does not alter `raw`,
  // so the RawSessionLine.message references stay byte-identical to disk.
  const resolvedMessages = resolveToolResultNames(messages);

  // Re-link the resolved messages back into rawLines (they may carry toolName).
  let resolvedIndex = 0;
  for (let i = 0; i < rawLines.length; i++) {
    const entry = rawLines[i];
    if (entry.kind === "message") {
      rawLines[i] = { ...entry, message: resolvedMessages[resolvedIndex] };
      resolvedIndex++;
    }
  }

  const totalTokens = resolvedMessages.reduce(
    (sum, msg) => sum + msg.tokenCount,
    0,
  );

  return {
    rawLines,
    messages: resolvedMessages,
    totalTokens,
    notFound: false,
  };
}

/**
 * Write a list of {@link RawSessionLine}s back to a JSONL file atomically.
 *
 * Writes to a per-process `.tmp` sibling first, then renames over the
 * destination so a crash mid-write can never leave a half-written file (the
 * empty-body case is also written via the temp+rename path for the same
 * guarantee). Each line is written verbatim from its `raw` field — no
 * re-serialization — so non-message entries are preserved. Note: the read path
 * normalizes line endings and drops blank lines, so this is content-fidelity
 * (every line preserved with relative order), not strict byte-for-byte
 * fidelity — sufficient and intended for OpenClaw's compact-JSON transcript.
 */
export async function writeSessionFileRaw(
  filePath: string,
  rawLines: readonly RawSessionLine[],
): Promise<void> {
  const dir = dirname(filePath);
  await mkdir(dir, { recursive: true });

  const body = rawLines.length === 0
    ? ""
    : `${rawLines.map((line) => line.raw).join("\n")}\n`;
  // Unique per-process temp path avoids two concurrent writers of the same
  // destination racing on a shared `.tmp` and one clobbering the other. PID +
  // hrtime handle concurrency within a host without needing the clock.
  const tmpPath = `${filePath}.${process.pid}.${process.hrtime.bigint()}.tmp`;
  await writeFile(tmpPath, body, "utf8");
  await rename(tmpPath, filePath);
}

/**
 * Best-effort extraction of the `type` field from a JSONL line.
 * Returns undefined for empty or non-JSON lines.
 */
function extractLineType(trimmedLine: string): string | undefined {
  if (!trimmedLine) return undefined;
  try {
    const parsed = JSON.parse(trimmedLine) as Record<string, unknown>;
    return typeof parsed.type === "string" ? parsed.type : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Create a system message SessionMessage (for compaction notices).
 */
export function createSystemMessage(text: string): SessionMessage {
  const raw = JSON.stringify({
    type: "message",
    timestamp: new Date().toISOString(),
    message: {
      role: "system",
      content: [{ type: "text", text }],
    },
  });

  return {
    role: "system",
    raw,
    text,
    isToolResult: false,
    isToolUse: false,
    isError: false,
    toolName: undefined,
    tokenCount: estimateTextTokens(text) + 4,
    contentBlocks: [{ type: "text", text }],
    timestamp: new Date().toISOString(),
  };
}

/**
 * Modify a tool_result message's content in-place.
 * Creates a new SessionMessage with the modified content.
 */
export function modifyToolResultContent(
  msg: SessionMessage,
  newContent: string,
): SessionMessage {
  if (!msg.isToolResult) return msg;

  // Reconstruct the raw JSON with modified content
  try {
    const parsed = JSON.parse(msg.raw) as Record<string, unknown>;
    const message = typeof parsed.message === "object" && parsed.message !== null
      ? { ...(parsed.message as Record<string, unknown>) }
      : parsed;

    const content = Array.isArray(message.content)
      ? (message.content as Record<string, unknown>[]).map((block) => {
          if ((block as Record<string, unknown>).type === "tool_result") {
            return { ...block, content: newContent };
          }
          return block;
        })
      : message.content;

    const modifiedMessage = { ...message, content };
    const raw = JSON.stringify({ ...parsed, message: modifiedMessage });

    return {
      ...msg,
      raw,
      text: newContent,
      tokenCount: estimateTextTokens(newContent) + 4,
      contentBlocks: parseContentBlocks(content),
    };
  } catch {
    // If we can't parse, return the message with modified text only
    return {
      ...msg,
      text: newContent,
      tokenCount: estimateTextTokens(newContent) + 4,
    };
  }
}

/**
 * Count token estimates for an array of messages.
 */
export function estimateSessionTokens(messages: readonly SessionMessage[]): number {
  return messages.reduce((sum, msg) => sum + msg.tokenCount, 0);
}

/**
 * Resolve tool names for tool_result messages by matching tool_use_id
 * with preceding tool_use blocks.
 *
 * This is a post-parse pass that enriches tool_result messages with
 * the tool name from their corresponding tool_use message. Without this,
 * tool_result messages have toolName=undefined because the name is only
 * present in the preceding tool_use block.
 *
 * Returns a new array with enriched messages (no mutation).
 */
export function resolveToolResultNames(
  messages: readonly SessionMessage[],
): SessionMessage[] {
  // Build a map of tool_use_id → toolName from tool_use blocks
  const toolUseIdToName = new Map<string, string>();

  for (const msg of messages) {
    if (!msg.isToolUse) continue;
    for (const block of msg.contentBlocks) {
      const type = (block as Record<string, unknown>).type;
      if (type === "tool_use" || type === "toolCall") {
        const id = (block as ToolUseBlock).id;
        const name = (block as ToolUseBlock).name;
        if (id && name) {
          toolUseIdToName.set(id, name);
        }
      }
    }
  }

  // Enrich tool_result messages with the resolved tool name
  return messages.map((msg) => {
    if (!msg.isToolResult || msg.toolName) return msg;

    // Try to find the tool_use_id in the content blocks
    for (const block of msg.contentBlocks) {
      const type = (block as Record<string, unknown>).type;
      if (type === "tool_result") {
        const toolUseId = (block as ToolResultBlock).tool_use_id;
        if (toolUseId) {
          const resolvedName = toolUseIdToName.get(toolUseId);
          if (resolvedName) {
            return { ...msg, toolName: resolvedName };
          }
        }
      }
    }

    return msg;
  });
}