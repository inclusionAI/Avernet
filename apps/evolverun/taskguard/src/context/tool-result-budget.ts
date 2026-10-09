/**
 * Age-based tool result budget truncation for session messages.
 *
 * Inspired by Claw Compactor's ToolResultBudget: older tool results are
 * truncated to short summaries, while recent results are preserved intact
 * (unless oversized). Tool types in the exemption list are never truncated.
 *
 * This provides a simpler, more consistent age-based strategy compared to
 * the pattern-based tool-output-prepass rules. It runs AFTER the prepass
 * so that content-aware rules (JSON sampling, log folding, etc.) get a
 * chance first, and this acts as a budget-based safety net.
 *
 * @module context/tool-result-budget
 */

import type { SessionMessage } from "./session-reader.js";
import { modifyToolResultContent, estimateSessionTokens } from "./session-reader.js";
import { estimateTextTokens } from "./token-counter.js";

// ── Configuration ──

/** Tool names whose results should NEVER be truncated. */
export const DEFAULT_EXEMPT_TOOLS: ReadonlySet<string> = new Set([
  "mcp",         // MCP tool results — critical for agent workflows
  "memory",      // Memory retrieval — user-context dependent
  "rewind",      // Rewind store lookups — lossy truncation loses retrievability
  "agent",       // Sub-agent outputs — already compressed by sub-agent
]);

/** Default number of most-recent tool results to keep untruncated. */
export const DEFAULT_KEEP_RECENT = 5;

/** Hard cap per tool result — even recent results exceeding this are trimmed. */
export const DEFAULT_MAX_TOOL_RESULT_TOKENS = 8000;

/** Preview length (chars) for oversized recent results. */
export const OVERSIZED_PREVIEW_CHARS = 200;

// ── Types ──

/** Options for budgetToolResults. */
export type ToolResultBudgetOptions = {
  /** Number of most-recent tool results to keep untruncated. Default: 5 */
  keepRecent?: number;
  /** Hard cap on tokens per tool result. Default: 8000 */
  maxTokensPerResult?: number;
  /** Tool names whose results are never truncated. */
  exemptTools?: ReadonlySet<string>;
};

/** Result of applying tool result budget truncation. */
export type ToolResultBudgetResult = {
  /** Messages after truncation (new array, no mutation). */
  messages: SessionMessage[];
  /** Number of old tool results truncated to one-line summaries. */
  truncatedCount: number;
  /** Number of recent but oversized results trimmed. */
  oversizedCount: number;
  /** Estimated tokens saved by truncation. */
  tokensSaved: number;
  /** Names of tools that were truncated/trimmed. */
  toolsAffected: string[];
};

// ── Core ──

/**
 * Apply age-based budget truncation to tool result messages.
 *
 * Strategy:
 * 1. Identify all tool_result messages in the list
 * 2. The last `keepRecent` tool results are "recent" and protected
 * 3. Recent but oversized (exceeding maxTokensPerResult) → preview + truncation notice
 * 4. Non-recent (old) → one-line summary with original size
 * 5. Exempt tools → skip entirely
 *
 * Returns a new messages array (immutable — input is not mutated).
 */
export function budgetToolResults(
  messages: readonly SessionMessage[],
  options?: ToolResultBudgetOptions,
): ToolResultBudgetResult {
  const keepRecent = options?.keepRecent ?? DEFAULT_KEEP_RECENT;
  const maxTokens = options?.maxTokensPerResult ?? DEFAULT_MAX_TOOL_RESULT_TOKENS;
  const exemptTools = options?.exemptTools ?? DEFAULT_EXEMPT_TOOLS;

  // Identify all tool_result message indices
  const toolIndices: number[] = [];
  for (let i = 0; i < messages.length; i++) {
    if (messages[i].isToolResult) {
      toolIndices.push(i);
    }
  }

  if (toolIndices.length === 0) {
    return {
      messages: [...messages],
      truncatedCount: 0,
      oversizedCount: 0,
      tokensSaved: 0,
      toolsAffected: [],
    };
  }

  // The last `keepRecent` tool messages are "recent" and protected
  const recentSet = new Set<number>(
    keepRecent > 0 ? toolIndices.slice(-keepRecent) : [],
  );

  const result: SessionMessage[] = [...messages];
  let truncatedCount = 0;
  let oversizedCount = 0;
  let tokensSaved = 0;
  const toolsAffected: string[] = [];

  for (const idx of toolIndices) {
    const msg = messages[idx];
    if (!msg.isToolResult) continue;

    // Check exemption by tool name
    const toolName = msg.toolName?.toLowerCase() ?? "";
    if (isExempt(toolName, exemptTools)) {
      continue;
    }

    const originalTokens = msg.tokenCount;
    const isRecent = recentSet.has(idx);

    // Case 1: Recent but oversized → trim to preview + truncation notice
    if (isRecent && originalTokens > maxTokens) {
      const preview = msg.text.slice(0, OVERSIZED_PREVIEW_CHARS).replace(/\s+$/, "");
      const truncated = `${preview}...\n\n[truncated from ${originalTokens} tokens — result too large, use read_file for full content]`;
      const newTokens = estimateTextTokens(truncated) + 4;

      result[idx] = modifyToolResultContent(msg, truncated);
      oversizedCount++;
      tokensSaved += originalTokens - newTokens;
      if (msg.toolName) toolsAffected.push(msg.toolName);
      continue;
    }

    // Case 2: Non-recent (old) tool result → full truncation to one-line summary
    if (!isRecent) {
      const truncated = `[tool result truncated — was ${originalTokens} tokens, ${msg.text.length} chars]`;
      const newTokens = estimateTextTokens(truncated) + 4;

      result[idx] = modifyToolResultContent(msg, truncated);
      truncatedCount++;
      tokensSaved += originalTokens - newTokens;
      if (msg.toolName) toolsAffected.push(msg.toolName);
    }
  }

  return {
    messages: result,
    truncatedCount,
    oversizedCount,
    tokensSaved,
    toolsAffected,
  };
}

/**
 * Check if a tool name matches any exempt pattern.
 *
 * Supports partial matching: "mcp__codegraph__search" matches "mcp".
 */
function isExempt(toolName: string, exemptTools: ReadonlySet<string>): boolean {
  if (toolName === "") return false;
  const exemptArray = Array.from(exemptTools);
  for (const exempt of exemptArray) {
    if (toolName === exempt || toolName.startsWith(`${exempt}__`) || toolName.startsWith(`${exempt}_`)) {
      return true;
    }
  }
  return false;
}