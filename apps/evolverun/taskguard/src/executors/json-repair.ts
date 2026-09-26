/**
 * Shared JSON repair utilities for embedded-agent and default-executor.
 *
 * Handles the most common LLM output formatting issues:
 *   - Markdown code fences (```json ... ```)
 *   - Non-JSON prefix/suffix text around JSON objects
 *   - Trailing commas before } or ]
 *   - Multiple disconnected JSON fragments mixed with prose
 *
 * @module executors/json-repair
 */

/**
 * Type guard for a plain object record.
 */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Try to parse text as JSON. Strips markdown code fences first.
 * Returns the parsed object or null if the text is not valid JSON.
 */
export function tryParseJson(text: string): Record<string, unknown> | null {
  let candidate = text.trim();

  // Strip markdown code fence (```json ... ```) — LLMs frequently wrap JSON
  // output in code fences despite "strict JSON" instructions.
  const fenceMatch = candidate.match(/```(?:json)?\s*\n?([\s\S]*?)\n?\s*```/);
  if (fenceMatch) candidate = fenceMatch[1]!.trim();

  if (!candidate.startsWith("{") || !candidate.endsWith("}")) return null;

  try {
    const parsed = JSON.parse(candidate);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * Lightweight, deterministic JSON repair without any LLM call.
 * Handles the most common formatting issues seen in embedded-agent output:
 *   1. Markdown code fences (```json ... ```)
 *   2. Non-JSON prefix/suffix text around the JSON object
 *   3. Trailing commas before } or ]
 *   4. Multiple disconnected JSON fragments (balanced-brace scan)
 *
 * Returns the repaired string if successful, or null if repair is not possible.
 */
export function lightweightJsonRepair(raw: string): string | null {
  let text = raw.trim();

  // 1. Strip markdown code fence
  const fenceMatch = text.match(/```(?:json)?\s*\n?([\s\S]*?)\n?\s*```/);
  if (fenceMatch) text = fenceMatch[1]!.trim();

  // 2. Extract the outermost { … } block
  // When LLM mixes multiple JSON fragments with explanatory text, the naive
  // firstBrace…lastBrace slice can span across disconnected fragments.  We
  // try the simple slice first; if it doesn't parse, fall back to scanning
  // for the first balanced { … } pair using a brace-depth counter.
  const firstBrace = text.indexOf("{");
  const lastBrace = text.lastIndexOf("}");
  if (firstBrace < 0 || lastBrace <= firstBrace) return null;

  let candidate = text.slice(firstBrace, lastBrace + 1);

  // 3. Remove trailing commas before } or ]
  candidate = candidate.replace(/,\s*([}\]])/g, "$1");

  // Quick parse attempt with the simple slice
  try {
    const parsed = JSON.parse(candidate);
    if (isRecord(parsed)) return candidate;
  } catch {
    // Simple slice didn't parse — try balanced-brace scan
  }

  // Balanced-brace scan: find the first complete { … } object by tracking
  // brace depth.  This correctly skips over nested objects and stops at the
  // first closing brace that balances the opening one, avoiding the case
  // where lastBrace is part of a later disconnected JSON fragment.
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === "{") depth++;
    else if (text[i] === "}") {
      depth--;
      if (depth === 0) {
        candidate = text.slice(firstBrace, i + 1);
        candidate = candidate.replace(/,\s*([}\]])/g, "$1");
        try {
          const parsed = JSON.parse(candidate);
          if (isRecord(parsed)) return candidate;
        } catch {
          // Even the balanced extraction didn't work — give up
        }
        break;
      }
    }
  }

  return null;
}

/**
 * Extract structured JSON result from an embedded-agent text output.
 *
 * Used when `outputMode: "json"` or an `outputContract` is defined on the node.
 * Tries direct parsing first, then falls back to lightweight repair.
 *
 * @returns The parsed JSON object, or null if neither parsing nor repair succeeds.
 */
export function extractJsonResult(output: string): Record<string, unknown> | null {
  const parsed = tryParseJson(output);
  if (parsed) return parsed;

  const repaired = lightweightJsonRepair(output);
  if (repaired) {
    const repairedParsed = tryParseJson(repaired);
    if (repairedParsed) return repairedParsed;
  }

  return null;
}