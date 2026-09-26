/**
 * Deterministic conversation summarization for session compression.
 *
 * Inspired by Claw Compactor's ConversationSummarizer: when a conversation
 * exceeds a token budget, older turns are collapsed into a structured summary
 * block. Unlike Claw Compactor (which uses LLM summarization), this module
 * uses pure deterministic extraction — no API calls, zero latency.
 *
 * Extraction captures:
 * - User instructions (first 200 chars each, last 10)
 * - Key decisions (regex patterns: "decided", "chose", "plan is", etc.)
 * - File paths referenced
 * - Functions/classes mentioned
 * - Error patterns
 * - Tool calls with token counts
 *
 * The summary replaces the original older turns with a single compact_boundary
 * system message, preserving the most recent turns verbatim.
 *
 * @module context/conversation-summarizer
 */

import type { SessionMessage } from "./session-reader.js";
import { createSystemMessage, estimateSessionTokens } from "./session-reader.js";
import { estimateTextTokens } from "./token-counter.js";

// ── Types ──

/** Options for conversation summarization. */
export type SummarizeOptions = {
  /** Number of recent user+assistant turn pairs to keep verbatim. Default: 4 */
  preserveRecentTurns?: number;
  /** Maximum tokens for the generated summary. Default: 5000 */
  maxSummaryTokens?: number;
  /** Minimum number of body messages to attempt summarization. Default: 4 */
  minBodyMessages?: number;
};

/** Result of conversation summarization. */
export type SummarizeResult = {
  /** Messages after summarization (new array, no mutation). */
  messages: SessionMessage[];
  /** Number of turns that were replaced by the summary. */
  turnsSummarized: number;
  /** Token count of the generated summary. */
  summaryTokens: number;
  /** Whether summarization was triggered. */
  triggered: boolean;
};

// ── Constants ──

const DEFAULT_PRESERVE_RECENT_TURNS = 4;
const DEFAULT_MAX_SUMMARY_TOKENS = 5000;
const DEFAULT_MIN_BODY_MESSAGES = 4;
const MAX_USER_INSTRUCTIONS = 10;
const MAX_DECISIONS = 10;
const MAX_ACTIONS = 15;
const MAX_FILES = 20;
const MAX_ERRORS = 5;
const MAX_USER_INSTRUCTION_CHARS = 200;

// ── Extraction Patterns ──

/** File path pattern: /path/to/file.ext */
const FILE_PATH_RE = /[`"']?(\/[\w./-]+\.\w{1,10})[`"']?/g;

/** Decision pattern: "decided", "chose", "will use", "plan is", "approach:" */
const DECISION_RE = /(?:decided|decision|chose|choosing|will use|going with|plan is|approach:|let's use|let's go with|we should|recommend(?:ation)? is)\s+(.{10,120})/gi;

/** Error pattern: "Error:", "Exception:", "FAIL:", etc. */
const ERROR_RE = /(?:Error|Exception|FAIL|failed|panic|fatal|critical)[:.\s]\s*(.{10,100})/gi;

/** Function/class definition pattern */
const FUNCTION_RE = /(?:def |function |class |fn |func |pub fn |mod |impl |struct |enum |interface |type )(\w+)/g;

// ── Public API ──

/**
 * Summarize older conversation turns, replacing them with a structured summary.
 *
 * Splits messages into:
 * 1. System messages at the start (preserved verbatim)
 * 2. Body messages (summarized)
 * 3. Recent tail messages (preserved verbatim)
 *
 * The body is extracted into a structured summary. If the body has too few
 * messages or the summary would exceed the token budget, messages are returned
 * unchanged.
 *
 * Returns a new messages array (immutable — input is not mutated).
 */
export function summarizeOldTurns(
  messages: readonly SessionMessage[],
  options?: SummarizeOptions,
): SummarizeResult {
  const preserveRecent = options?.preserveRecentTurns ?? DEFAULT_PRESERVE_RECENT_TURNS;
  const maxSummaryTokens = options?.maxSummaryTokens ?? DEFAULT_MAX_SUMMARY_TOKENS;
  const minBodyMessages = options?.minBodyMessages ?? DEFAULT_MIN_BODY_MESSAGES;

  const totalTokens = estimateSessionTokens(messages);

  // Split into system prefix, body, and recent tail
  const { systemMsgs, bodyMsgs, recentMsgs } = splitMessages(messages, preserveRecent);

  const result: SummarizeResult = {
    messages: [...messages], // Default: return unchanged
    turnsSummarized: 0,
    summaryTokens: 0,
    triggered: false,
  };

  // Not enough body messages to summarize
  if (bodyMsgs.length < minBodyMessages) {
    return result;
  }

  // Extract structured summary from body
  const summaryLines = extractSummary(bodyMsgs);
  let summaryText = summaryLines.join("\n");

  // Enforce max summary tokens
  let summaryTokens = estimateTextTokens(summaryText);
  if (summaryTokens > maxSummaryTokens) {
    // Truncate from the end, keeping section headers
    while (summaryTokens > maxSummaryTokens && summaryLines.length > 5) {
      summaryLines.pop();
      summaryText = summaryLines.join("\n") + "\n[...truncated summary]";
      summaryTokens = estimateTextTokens(summaryText);
    }
  }

  // Build the compact_boundary system message
  const originalBodyTokens = estimateSessionTokens(bodyMsgs);
  const boundaryMsg = createCompactBoundary(summaryText, bodyMsgs.length, originalBodyTokens);

  // Reassemble: system prefix + boundary message + recent tail
  const newMessages = [...systemMsgs, boundaryMsg, ...recentMsgs];

  return {
    messages: newMessages,
    turnsSummarized: bodyMsgs.length,
    summaryTokens,
    triggered: true,
  };
}

// ── Internal Helpers ──

/**
 * Split messages into (system_prefix, compactable_body, recent_tail).
 *
 * - System messages at the start form the system prefix
 * - The last N user messages and their associated assistant/tool messages form the recent tail
 * - Everything in between is the body to be summarized
 */
function splitMessages(
  messages: readonly SessionMessage[],
  preserveRecentTurns: number,
): { systemMsgs: SessionMessage[]; bodyMsgs: SessionMessage[]; recentMsgs: SessionMessage[] } {
  // System messages at the start
  const systemMsgs: SessionMessage[] = [];
  let i = 0;
  while (i < messages.length && messages[i].role === "system") {
    systemMsgs.push(messages[i]);
    i++;
  }

  const remaining = messages.slice(i);

  if (preserveRecentTurns <= 0) {
    return { systemMsgs, bodyMsgs: remaining, recentMsgs: [] };
  }

  // Walk backwards counting user messages as turn boundaries
  let turnsFound = 0;
  let splitIdx = remaining.length;

  for (let j = remaining.length - 1; j >= 0; j--) {
    if (remaining[j].role === "user" && !remaining[j].isToolResult) {
      turnsFound++;
      if (turnsFound >= preserveRecentTurns) {
        splitIdx = j;
        break;
      }
    }
  }

  const bodyMsgs = remaining.slice(0, splitIdx);
  const recentMsgs = remaining.slice(splitIdx);

  return { systemMsgs, bodyMsgs, recentMsgs };
}

/**
 * Extract a structured summary from a list of conversation messages.
 *
 * Extracts: user instructions, decisions, actions, file paths, errors.
 * All extraction is deterministic (regex-based) — no LLM calls.
 */
function extractSummary(messages: readonly SessionMessage[]): string[] {
  const lines: string[] = ["## 对话摘要（自动压缩）", ""];

  const decisions: string[] = [];
  const filesMentioned = new Set<string>();
  const functionsMentioned = new Set<string>();
  const errors: string[] = [];
  const userInstructions: string[] = [];
  const actionsTaken: string[] = [];

  for (const msg of messages) {
    const content = msg.text;

    // Extract based on role
    if (msg.role === "user" && !msg.isToolResult) {
      // Preserve user instructions (capped)
      const trimmed = content.trim().slice(0, MAX_USER_INSTRUCTION_CHARS);
      if (trimmed.length > 0) {
        userInstructions.push(trimmed);
      }
    } else if (msg.role === "assistant") {
      // Extract decisions
      const decMatches = [...content.matchAll(DECISION_RE)];
      for (const m of decMatches) {
        decisions.push(m[1].trim());
      }
      // Extract first line as action summary
      const firstLine = content.split("\n")[0]?.slice(0, 150).trim();
      if (firstLine) {
        actionsTaken.push(firstLine);
      }
    } else if (msg.isToolResult) {
      // One-line summary for tool results
      const toolName = msg.toolName ?? "tool";
      actionsTaken.push(`[${toolName}: ${msg.tokenCount} tokens]`);
    }

    // Extract file paths, functions, and errors from any role
    const fileMatches = [...content.matchAll(FILE_PATH_RE)];
    for (const m of fileMatches) {
      filesMentioned.add(m[1]);
    }

    const funcMatches = [...content.matchAll(FUNCTION_RE)];
    for (const m of funcMatches) {
      functionsMentioned.add(m[1]);
    }

    const errMatches = [...content.matchAll(ERROR_RE)];
    for (const m of errMatches) {
      errors.push(m[1].trim().slice(0, 100));
    }
  }

  // Build summary sections
  if (userInstructions.length > 0) {
    lines.push("### 用户指令");
    for (const instr of userInstructions.slice(-MAX_USER_INSTRUCTIONS)) {
      lines.push(`- ${instr}`);
    }
    lines.push("");
  }

  if (decisions.length > 0) {
    lines.push("### 关键决策");
    for (const d of decisions.slice(-MAX_DECISIONS)) {
      lines.push(`- ${d}`);
    }
    lines.push("");
  }

  if (actionsTaken.length > 0) {
    lines.push("### 执行的操作");
    for (const a of actionsTaken.slice(-MAX_ACTIONS)) {
      lines.push(`- ${a}`);
    }
    lines.push("");
  }

  if (filesMentioned.size > 0) {
    lines.push("### 引用文件");
    const sortedFiles = Array.from(filesMentioned).sort().slice(0, MAX_FILES);
    for (const f of sortedFiles) {
      lines.push(`- \`${f}\``);
    }
    lines.push("");
  }

  if (functionsMentioned.size > 0) {
    lines.push("### 相关函数");
    const sortedFuncs = Array.from(functionsMentioned).sort().slice(0, MAX_FILES);
    for (const f of sortedFuncs) {
      lines.push(`- ${f}()`);
    }
    lines.push("");
  }

  if (errors.length > 0) {
    lines.push("### 遇到的错误");
    for (const e of errors.slice(-MAX_ERRORS)) {
      lines.push(`- ${e}`);
    }
    lines.push("");
  }

  return lines;
}

/**
 * Create a compact_boundary system message.
 *
 * Format compatible with Claude Code's AutoCompact system — downstream
 * consumers can detect and handle compacted history by checking for
 * the subtype "compact_boundary".
 */
function createCompactBoundary(
  summary: string,
  turnsSummarized: number,
  originalTokens: number,
): SessionMessage {
  const content = [
    summary,
    "",
    `---`,
    `[压缩元数据: 原始${turnsSummarized}条消息, ${originalTokens} tokens → ${estimateTextTokens(summary)} tokens]`,
  ].join("\n");

  return createSystemMessage(content);
}