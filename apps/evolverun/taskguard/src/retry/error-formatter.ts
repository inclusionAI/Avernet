/**
 * Error recovery context formatting for prompt injection.
 *
 * Formats KB results and retry directives for injection into the
 * next retry's template context as `errorRecoveryContext`.
 *
 * Adapted from ClawMind's formatErrorContext for ClawFlow.
 */

import type { PendingErrorContext } from "./error-context-store.js";

/**
 * Format error recovery context from pending error entries.
 *
 * Produces a structured markdown block that summarizes recent failures
 * and any KB hints for the retrying node.
 */
export function formatErrorRecoveryContext(
  flowId: string,
  nodeId: string,
  entries: PendingErrorContext[],
): string {
  if (entries.length === 0) return "";

  const lines: string[] = ["[ClawMind Error Recovery Context]", ""];

  // Format each error entry
  const relevant = entries.filter(
    (e) => e.flowId === flowId && e.nodeId === nodeId,
  );

  if (relevant.length === 0) return "";

  for (const entry of relevant) {
    lines.push(`**Attempt ${entry.attempt}** failed: ${entry.error}`);

    if (entry.kbResults) {
      lines.push("");
      lines.push("Knowledge Base Suggestions:");
      lines.push(entry.kbResults);
    }

    lines.push("");
    lines.push("---");
    lines.push("");
  }

  lines.push("Review the above errors and suggestions. Adjust the approach to avoid repeating the same failure.");

  return lines.join("\n");
}

/**
 * Format a simple error context for the auto-retry case (no KB results).
 */
export function formatSimpleErrorContext(
  nodeId: string,
  attempt: number,
  error: string,
): string {
  return [
    "[ClawMind Error Context]",
    "",
    `Node "${nodeId}" failed on attempt ${attempt}: ${error.slice(0, 500)}`,
    "",
    "Retrying with error awareness. Consider adjusting the approach.",
  ].join("\n");
}