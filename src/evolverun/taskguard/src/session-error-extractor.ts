/**
 * Post-execution error extraction from embedded-agent session JSONL files.
 *
 * After `executeEmbeddedAgent` completes successfully (status="succeeded"),
 * the .jsonl session file may still contain tool errors or API errors that
 * the agent "recovered from" — producing output despite partial failures.
 * These are invisible to the workflow engine today, creating "false success".
 *
 * This module reads the session file and extracts a structured error summary
 * that can be attached as warnings to the ExecutorResult, surfacing tool errors
 * in the `node_executions.error_text` column.
 *
 * @module session-error-extractor
 */

import { readFileSync } from "node:fs";

// ── Types ──────────────────────────────────────────────────────────────────

export type SessionToolError = {
  /** Tool name or tool_use_id from the error payload. */
  toolName: string;
  /** Truncated error message text. */
  errorMessage: string;
  /** ISO timestamp from the JSONL line, if present. */
  timestamp?: string;
};

export type SessionApiError = {
  /** Error code (e.g. "429", "timeout", "rate_limit"). */
  errorCode: string;
  /** Error message. */
  errorMessage: string;
  /** ISO timestamp from the JSONL line, if present. */
  timestamp?: string;
};

export type SessionErrorSummary = {
  /** Individual tool errors found in the session file. */
  toolErrors: SessionToolError[];
  /** API-level errors found in the session file. */
  apiErrors: SessionApiError[];
  /** True if any errors were found. */
  hasErrors: boolean;
  /** Total error count (toolErrors.length + apiErrors.length). */
  errorCount: number;
};

// ── Config ─────────────────────────────────────────────────────────────────

const MAX_TOOL_ERRORS = 10;
const MAX_API_ERRORS = 5;
const MAX_ERROR_MESSAGE_LENGTH = 500;
const MAX_JSONL_FILE_SIZE = 10 * 1024 * 1024; // 10 MB safety limit

// ── Helpers ────────────────────────────────────────────────────────────────

function truncateText(value: string, max = MAX_ERROR_MESSAGE_LENGTH): string {
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
}

// ── Extraction ─────────────────────────────────────────────────────────────

/**
 * Extract tool and API errors from an embedded-agent session JSONL file.
 *
 * This reads the `.jsonl` file produced by `runEmbeddedPiAgent` and scans for:
 * 1. **Tool result errors** — `message.role === "tool"` with `isError === true`
 *    or content blocks of type `"tool_result"` with `is_error === true`.
 * 2. **API-level errors** — top-level `type === "error"` records or records
 *    containing an `error` field with a truthy string value.
 *
 * @param sessionFile Path to the `.jsonl` session file. If undefined or the
 *                    file cannot be read, returns an empty summary.
 * @returns A structured summary of errors found in the session file.
 */
export function extractSessionErrors(sessionFile: string | undefined): SessionErrorSummary {
  const summary: SessionErrorSummary = { toolErrors: [], apiErrors: [], hasErrors: false, errorCount: 0 };
  if (!sessionFile) return summary;

  try {
    let content: string;
    try {
      // Read with size check to avoid processing gigantic files
      const buffer = readFileSync(sessionFile);
      if (buffer.length > MAX_JSONL_FILE_SIZE) {
        // Only read the last portion of very large files — errors are often
        // near the end when the agent recovers from an error.
        content = buffer.slice(-MAX_JSONL_FILE_SIZE).toString("utf8");
        // Skip the first (potentially partial) line
        const firstNewline = content.indexOf("\n");
        if (firstNewline >= 0) content = content.slice(firstNewline + 1);
      } else {
        content = buffer.toString("utf8");
      }
    } catch {
      return summary;
    }

    const lines = content.split(/\r?\n/);

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      let record: Record<string, unknown>;
      try {
        record = JSON.parse(trimmed) as Record<string, unknown>;
      } catch {
        continue;
      }

      // ── Tool result errors ──
      const message = isRecord(record.message) ? record.message : undefined;
      const role = asString(message?.role ?? record.role);

      if (role === "tool") {
        extractToolResultErrors(message ?? record, summary);
      }

      // Also check assistant messages for tool_use blocks that had errors
      // in their subsequent tool_result (the result is a separate line, but
      // we can detect patterns in the content blocks)
      if (role === "user") {
        extractToolResultErrors(message ?? record, summary);
      }

      // ── API-level errors ──
      const recordType = asString(record.type);
      if (recordType === "error" || (recordType === "api_error")) {
        summary.apiErrors.push({
          errorCode: asString(record.code ?? record.status ?? "api_error") ?? "api_error",
          errorMessage: truncateText(asString(record.message ?? record.error ?? "") ?? "Unknown API error"),
          timestamp: asString(record.timestamp),
        });
        if (summary.apiErrors.length >= MAX_API_ERRORS) break;
      }

      // Records with a top-level `error` string field (not object)
      if (typeof record.error === "string" && record.error.trim()) {
        summary.apiErrors.push({
          errorCode: asString(record.code ?? record.errorCode ?? "runtime_error") ?? "runtime_error",
          errorMessage: truncateText(record.error),
          timestamp: asString(record.timestamp),
        });
        if (summary.apiErrors.length >= MAX_API_ERRORS) break;
      }
    }

    summary.errorCount = summary.toolErrors.length + summary.apiErrors.length;
    summary.hasErrors = summary.errorCount > 0;
    return summary;
  } catch {
    return summary;
  }
}

/**
 * Extract tool errors from a tool_result record.
 * Handles both OpenAI-style and Anthropic-style content blocks.
 */
function extractToolResultErrors(
  record: Record<string, unknown>,
  summary: SessionErrorSummary,
): void {
  const content = record.content;
  if (!Array.isArray(content)) return;

  for (const block of content) {
    if (!isRecord(block)) continue;

    // Anthropic-style: type="tool_result", is_error=true
    const isErrorFlag = block.is_error === true || block.isError === true;
    // Also check for error-type content blocks
    const isErrorType = asString(block.type) === "error";

    if (isErrorFlag || isErrorType) {
      if (summary.toolErrors.length >= MAX_TOOL_ERRORS) return;

      const toolName = asString(block.name ?? block.toolUseId ?? block.tool_use_id ?? "unknown_tool") ?? "unknown_tool";
      const errorText = extractErrorText(block);
      summary.toolErrors.push({
        toolName,
        errorMessage: truncateText(errorText),
        timestamp: asString(record.timestamp),
      });
    }
  }
}

/**
 * Extract error text from a tool result content block.
 */
function extractErrorText(block: Record<string, unknown>): string {
  // Direct text field
  const text = asString(block.text);
  if (text) return text;

  // Nested content array (Anthropic-style: content[{type:"text", text:"..."}])
  const nestedContent = block.content;
  if (Array.isArray(nestedContent)) {
    const parts: string[] = [];
    for (const item of nestedContent) {
      if (isRecord(item) && typeof item.text === "string") {
        parts.push(item.text);
      }
    }
    if (parts.length > 0) return parts.join("\n");
  }

  // String content
  if (typeof block.content === "string") return block.content;

  // Fallback: JSON representation
  try {
    return JSON.stringify(block);
  } catch {
    return "Unknown error";
  }
}