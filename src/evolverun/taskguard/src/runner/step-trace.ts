/**
 * Step trace extraction from embedded-agent session JSONL files.
 *
 * After `executeEmbeddedAgent` completes, the .jsonl session file contains
 * the full conversation record including every tool_use (input) and
 * tool_result (output). This module parses that file into a structured
 * step trace that can be persisted and exposed via API.
 *
 * Relationship to existing modules:
 * - `session-error-extractor.ts` reads the SAME file but only extracts errors
 * - `step-trace.ts` reads it for ALL steps (normal + error)
 * - Both are called after node execution completes, before Controller updates state
 *
 * @module step-trace
 */

import { readFileSync } from "node:fs";

// ── Types ──────────────────────────────────────────────────────────────────

export type StepRecord = {
  /** Step sequence number (1-based, in execution order). */
  seq: number;
  /** Step type. */
  type: "tool_call" | "tool_result" | "assistant_text";
  /** Tool name (only for tool_call / tool_result). */
  toolName?: string;
  /** Tool call ID (links tool_call ↔ tool_result). */
  toolUseId?: string;
  /** Tool input parameters (only for tool_call). */
  toolInput?: Record<string, unknown>;
  /** Tool result text output (only for tool_result, truncated). */
  toolOutput?: string;
  /** Whether the tool result is an error. */
  isError?: boolean;
  /** Assistant text content (only for assistant_text). */
  text?: string;
};

export type NodeStepTrace = {
  /** Node ID that produced this trace. */
  nodeId: string;
  /** Flow run ID. */
  flowId: string;
  /** Session file that was parsed. */
  sessionFile: string;
  /** Structured step records. */
  steps: StepRecord[];
  /** Number of tool calls in the trace. */
  toolCallCount: number;
  /** Number of tool errors in the trace. */
  toolErrorCount: number;
};

// ── Config ─────────────────────────────────────────────────────────────────

const MAX_STEPS = 100;
const MAX_OUTPUT_CHARS = 2000;
const MAX_JSONL_FILE_SIZE = 10 * 1024 * 1024; // 10 MB safety limit
const MAX_TOOL_INPUT_CHARS = 1000; // Truncate tool input for storage

// ── Payload Budget ────────────────────────────────────────────────────────
//
// The clawweb backend (Tengine) imposes a ~18KB body size limit on POST
// requests. With 14KB chunking (CHUNK_MAX_BYTES in
// node-step-traces-api-repository.ts), each sub-batch must fit within
// that budget. The per-step overhead (JSON keys + envelope) is ~300 bytes,
// so the effective content budget per step at 5 steps/chunk is:
//
//   (14,336 - envelope ~200) / 5 = ~2,800 bytes
//
// MAX_TOOL_INPUT_CHARS (1000) + MAX_OUTPUT_CHARS (2000) = 3,000 bytes
// of content per step, which fits comfortably within the budget while
// preserving enough diagnostic information for debugging.
//
// Previous values (2000 + 5000 = 7000 bytes/step) would allow only
// ~2 steps per 14KB chunk, causing excessive HTTP round-trips and
// still risking overflow for steps with both input and output present.

// ── Helpers ────────────────────────────────────────────────────────────────

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
}

function truncateText(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function truncateToolInput(
  input: Record<string, unknown>,
  maxChars: number,
): Record<string, unknown> {
  const json = JSON.stringify(input);
  if (json.length <= maxChars) return input;
  // Return a truncated representation
  return { _truncated: truncateText(json, maxChars) };
}

// ── Extraction ─────────────────────────────────────────────────────────────

/**
 * Extract structured step trace from an embedded-agent session JSONL file.
 *
 * This reads the `.jsonl` file produced by `runEmbeddedPiAgent` and parses
 * every conversation turn into structured StepRecords:
 *
 * 1. **assistant message with tool_use blocks** → StepRecord(type="tool_call")
 * 2. **user message with tool_result blocks** → StepRecord(type="tool_result")
 * 3. **assistant message with text blocks** → StepRecord(type="assistant_text")
 *
 * tool_call and tool_result are linked via `toolUseId`.
 *
 * @param sessionFile Path to the `.jsonl` session file.
 * @param nodeId Node ID for the trace metadata.
 * @param flowId Flow run ID for the trace metadata.
 * @returns Structured step trace, or null if the file cannot be read.
 */
export function extractNodeStepTrace(
  sessionFile: string | undefined,
  nodeId: string,
  flowId: string,
): NodeStepTrace | null {
  if (!sessionFile) return null;

  try {
    let content: string;
    try {
      const buffer = readFileSync(sessionFile);
      content = sliceJsonlContent(buffer);
    } catch {
      return null;
    }

    return extractNodeStepTraceFromContent(content, sessionFile, nodeId, flowId);
  } catch {
    return null;
  }
}

/**
 * Extract step trace from a pre-loaded JSONL content string.
 *
 * Use this overload when the JSONL content has already been read (e.g., to
 * avoid a race with OpenClaw's SessionManager writing back compressed state
 * between the `import().then()` microtask and the actual file read).
 *
 * @param content  The raw JSONL content (already read from disk).
 * @param sessionFile Original session file path (for metadata only).
 * @param nodeId   Node ID for the trace metadata.
 * @param flowId   Flow run ID for the trace metadata.
 * @returns Structured step trace, or null if content is empty.
 */
export function extractNodeStepTraceFromContent(
  content: string,
  sessionFile: string,
  nodeId: string,
  flowId: string,
): NodeStepTrace | null {
  if (!content) return null;

  try {
    const trace = parseStepTraceFromJsonl(content, sessionFile, nodeId, flowId);
    if (trace.steps.length === 0) {
      // Dump first 5 non-empty lines to diagnose format mismatch
      const sample = content.split(/\r?\n/).filter((l) => l.trim()).slice(0, 5);
      console.warn(
        `[step-trace] extractNodeStepTraceFromContent: 0 steps extracted!\n` +
        `  contentLen=${content.length} sessionFile=${sessionFile}\n` +
        sample.map((l, i) => `  [${i}] ${l.slice(0, 300)}`).join("\n"),
      );
    }
    return trace;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    console.error(`[step-trace] extractNodeStepTraceFromContent: parse threw — ${msg} sessionFile=${sessionFile}`);
    return null;
  }
}

// ── Internal: slice a JSONL buffer respecting the size limit ────────────────

function sliceJsonlContent(buffer: Buffer): string {
  if (buffer.length > MAX_JSONL_FILE_SIZE) {
    const content = buffer.slice(-MAX_JSONL_FILE_SIZE).toString("utf8");
    const firstNewline = content.indexOf("\n");
    return firstNewline >= 0 ? content.slice(firstNewline + 1) : content;
  }
  return buffer.toString("utf8");
}

// ── Internal: parse JSONL content into a NodeStepTrace ─────────────────────

function parseStepTraceFromJsonl(
  content: string,
  sessionFile: string,
  nodeId: string,
  flowId: string,
): NodeStepTrace {
  const trace: NodeStepTrace = {
    nodeId,
    flowId,
    sessionFile,
    steps: [],
    toolCallCount: 0,
    toolErrorCount: 0,
  };

  const lines = content.split(/\r?\n/);
  let parseErrors = 0;
  let noRole = 0;
  let assistantMsgs = 0;
  let toolResultMsgs = 0;
  let userMsgs = 0;

  // Track pending tool_calls by ID so we can link tool_results
  const pendingToolCalls = new Map<string, { seq: number; name: string }>();

  for (const line of lines) {
    if (trace.steps.length >= MAX_STEPS) break;

    const trimmed = line.trim();
    if (!trimmed) continue;

    let record: Record<string, unknown>;
    try {
      record = JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      parseErrors += 1;
      continue;
    }

    const message = isRecord(record.message) ? record.message : undefined;
    const role = asString(message?.role ?? record.role);
    if (!message || !role) {
      noRole += 1;
      continue;
    }

    // ── Assistant message ──
    if (role === "assistant") {
      assistantMsgs += 1;
      const msgContent = message.content ?? record.content;
      if (!Array.isArray(msgContent)) continue;

      for (const block of msgContent) {
        if (!isRecord(block)) continue;

        // OpenClaw uses "toolCall" with "arguments"; Anthropic uses "tool_use" with "input"
        const isToolCall = block.type === "toolCall" || block.type === "tool_use";
        if (isToolCall) {
          const toolName = asString(block.name) ?? "unknown_tool";
          const toolUseId = asString(block.id) ?? asString(block.tool_use_id) ?? `auto_${trace.steps.length + 1}`;
          // OpenClaw: block.arguments; Anthropic: block.input
          const toolInput = isRecord(block.arguments) ? block.arguments
            : isRecord(block.input) ? block.input : undefined;

          trace.toolCallCount += 1;
          const step: StepRecord = {
            seq: trace.steps.length + 1,
            type: "tool_call",
            toolName,
            toolUseId,
            toolInput: toolInput
              ? truncateToolInput(toolInput, MAX_TOOL_INPUT_CHARS)
              : undefined,
          };
          trace.steps.push(step);

          // Track for linking with tool_result
          pendingToolCalls.set(toolUseId, { seq: step.seq, name: toolName });
        } else if (block.type === "text" && typeof block.text === "string" && block.text.trim()) {
          // Assistant text step
          trace.steps.push({
            seq: trace.steps.length + 1,
            type: "assistant_text",
            text: truncateText(block.text, MAX_OUTPUT_CHARS),
          });
        }
      }
    }

    // ── Tool result message ──
    // OpenClaw format: role="toolResult", toolCallId, content=[{type:"text"}], isError
    // Anthropic format: role="user", content=[{type:"tool_result", tool_use_id, ...}]
    const isToolResultRole = role === "toolResult";
    if (isToolResultRole) {
      toolResultMsgs += 1;
    } else if (role === "user") {
      userMsgs += 1;
    }
    if (isToolResultRole || role === "user") {
      const msgContent = message.content ?? record.content;

      if (isToolResultRole) {
        // OpenClaw format: toolResult is a top-level message with toolCallId
        const toolUseId = asString(message.toolCallId ?? message.tool_use_id ?? message.toolUseId) ?? "";
        const toolName = asString(message.toolName ?? message.name) ?? "unknown_tool";
        const isError = message.isError === true || message.is_error === true;
        const toolOutput = extractToolResultTextFromMessage(message);

        if (isError) trace.toolErrorCount += 1;

        const pending = toolUseId ? pendingToolCalls.get(toolUseId) : undefined;
        if (pending) {
          pendingToolCalls.delete(toolUseId);
        }

        trace.steps.push({
          seq: trace.steps.length + 1,
          type: "tool_result",
          toolName: pending?.name ?? toolName,
          toolUseId: toolUseId || undefined,
          toolOutput: truncateText(toolOutput, MAX_OUTPUT_CHARS),
          isError,
        });
      } else if (Array.isArray(msgContent)) {
        // Anthropic format: user message contains tool_result blocks
        for (const block of msgContent) {
          if (!isRecord(block)) continue;

          if (block.type === "tool_result" || block.role === "tool") {
            const toolUseId = asString(block.tool_use_id ?? block.toolUseId) ?? "";
            const isError = block.is_error === true || block.isError === true;

            const toolOutput = extractToolResultText(block);

            if (isError) trace.toolErrorCount += 1;

            const pending = toolUseId ? pendingToolCalls.get(toolUseId) : undefined;
            if (pending) {
              pendingToolCalls.delete(toolUseId);
            }

            trace.steps.push({
              seq: trace.steps.length + 1,
              type: "tool_result",
              toolName: pending?.name ?? asString(block.name) ?? "unknown_tool",
              toolUseId: toolUseId || undefined,
              toolOutput: truncateText(toolOutput, MAX_OUTPUT_CHARS),
              isError,
            });
          }
        }
      }
    }
  }

  console.log(
    `[step-trace] parseStepTraceFromJsonl summary: ` +
    `totalLines=${lines.length} parseErrors=${parseErrors} noRole=${noRole} ` +
    `assistant=${assistantMsgs} toolResult=${toolResultMsgs} user=${userMsgs} ` +
    `stepsExtracted=${trace.steps.length} toolCalls=${trace.toolCallCount} ` +
    `toolErrors=${trace.toolErrorCount} sessionFile=${sessionFile}`,
  );

  return trace;
}

/**
 * Extract text content from an OpenClaw toolResult message.
 * OpenClaw stores tool results as: {role:"toolResult", content:[{type:"text",text:"..."}], ...}
 */
function extractToolResultTextFromMessage(message: Record<string, unknown>): string {
  const content = message.content;
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const item of content) {
      if (isRecord(item) && typeof item.text === "string" && item.text.trim()) {
        parts.push(item.text);
      }
    }
    if (parts.length > 0) return parts.join("\n");
  }
  // Direct text or string content
  if (typeof content === "string") return content;
  const text = asString(message.text);
  if (text) return text;
  return "";
}

/**
 * Extract text content from a tool_result block.
 * Handles Anthropic-style content arrays and plain text.
 */
function extractToolResultText(block: Record<string, unknown>): string {
  // Direct text field
  const text = asString(block.text);
  if (text) return text;

  // Anthropic-style content array
  const content = block.content;
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const item of content) {
      if (isRecord(item) && typeof item.text === "string" && item.text.trim()) {
        parts.push(item.text);
      }
    }
    if (parts.length > 0) return parts.join("\n");
  }

  // String content
  if (typeof block.content === "string") return block.content;

  return "";
}

/**
 * Quick count of tool calls and errors without full extraction.
 * Useful for lightweight metrics without parsing full step data.
 */
export function countStepStats(
  sessionFile: string | undefined,
): { toolCallCount: number; toolErrorCount: number; assistantTurns: number } | null {
  const trace = extractNodeStepTrace(sessionFile, "", "");
  if (!trace) return null;
  return {
    toolCallCount: trace.toolCallCount,
    toolErrorCount: trace.toolErrorCount,
    assistantTurns: trace.steps.filter((s) => s.type === "assistant_text").length,
  };
}