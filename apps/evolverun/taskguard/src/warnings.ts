/**
 * Warning formatting utilities for embedded-agent execution warnings.
 *
 * Warnings are stored in `node_executions.error_text` using a `[WARNINGS]` prefix
 * so they can be distinguished from actual failure errors by both the frontend
 * and any downstream consumers.
 *
 * @module warnings
 */

import type { ExecutionWarning } from "./types.js";

/**
 * Format an array of ExecutionWarning objects into a single string suitable
 * for the `node_executions.error_text` column.
 *
 * Format: `[WARNINGS][code1] message1 | detail1; [code2] message2`
 *
 * The `[WARNINGS]` prefix distinguishes this from genuine failure errors.
 * Returns `undefined` if the warnings array is empty or undefined.
 */
export function formatWarningsAsErrorText(warnings: ExecutionWarning[] | undefined): string | undefined {
  if (!warnings || warnings.length === 0) return undefined;
  return warnings
    .map((w) => {
      const detail = w.detail ? ` | ${JSON.stringify(w.detail)}` : "";
      return `[WARNINGS][${w.code}] ${w.message}${detail}`;
    })
    .join("; ");
}

/**
 * Check if an error_text value represents warnings (rather than a genuine failure).
 */
export function isWarningsErrorText(errorText: string | null | undefined): boolean {
  return typeof errorText === "string" && errorText.startsWith("[WARNINGS]");
}

/**
 * Parse a `[WARNINGS]`-prefixed error_text back into structured warning objects.
 * Returns an empty array if the string is not a warnings-prefixed value.
 */
export function parseWarningsErrorText(errorText: string | null | undefined): ExecutionWarning[] {
  if (!errorText || !isWarningsErrorText(errorText)) return [];

  const warnings: ExecutionWarning[] = [];
  // Split on "; [WARNINGS][" boundaries
  const segments = errorText.split("; [WARNINGS][");

  for (let i = 0; i < segments.length; i++) {
    const raw = i === 0
      ? segments[i]!.replace(/^\[WARNINGS]\[/, "")
      : segments[i]!;

    // Extract code: everything before the first "] "
    const codeEnd = raw.indexOf("] ");
    if (codeEnd < 0) continue;

    const code = raw.slice(0, codeEnd) as ExecutionWarning["code"];
    const rest = raw.slice(codeEnd + 2);

    // Split message from detail by " | "
    const detailSplit = rest.indexOf(" | {");
    let message: string;
    let detail: Record<string, unknown> | undefined;

    if (detailSplit >= 0) {
      message = rest.slice(0, detailSplit);
      const detailJson = rest.slice(detailSplit + 3); // skip " | "
      try {
        const parsed = JSON.parse(detailJson);
        detail = typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
          ? parsed as Record<string, unknown>
          : undefined;
      } catch {
        // If detail is not valid JSON, keep it as part of the message
        message = rest;
      }
    } else {
      message = rest;
    }

    warnings.push({ code, message, detail });
  }

  return warnings;
}