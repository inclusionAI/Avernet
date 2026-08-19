/**
 * LLM output YAML parser — handles three output formats from LLM responses:
 *
 * 1. Raw YAML (starts with `id:` or `workflow:` etc.)
 * 2. Fenced code block (```yaml ... ``` or ``` ... ```)
 * 3. JSON object (LLM returns JSON instead of YAML)
 *
 * If none of the above are found, returns a parseError result.
 */
import { parse as parseYaml } from "yaml";

// ── Public types ──

export type ParsedYamlResult =
  | { parsed: unknown; parseError?: undefined }
  | { parsed?: undefined; parseError: string };

// ── Public API ──

/**
 * Parse LLM output into a structured object.
 *
 * Tries extraction strategies in order:
 * 1. Fenced code block extraction (```yaml ... ``` or ``` ... ```)
 * 2. Direct YAML parse
 * 3. JSON parse (if output looks like JSON)
 *
 * Returns `{ parsed }` on success or `{ parseError }` on failure.
 */
export function parseLlmYamlOutput(raw: string): ParsedYamlResult {
  if (!raw || typeof raw !== "string") {
    return { parseError: "LLM output is empty or not a string" };
  }

  const trimmed = raw.trim();

  // Strategy 1: Extract from fenced code blocks
  const fencedResult = extractFencedCodeBlock(trimmed);
  if (fencedResult !== null) {
    const parsed = tryParseYaml(fencedResult);
    if (parsed !== null) {
      return { parsed };
    }
    // If YAML parse of fenced block failed, try JSON
    const jsonParsed = tryParseJson(fencedResult);
    if (jsonParsed !== null) {
      return { parsed: jsonParsed };
    }
    return { parseError: `Fenced code block found but content could not be parsed as YAML or JSON` };
  }

  // Strategy 2: Direct YAML parse
  const yamlParsed = tryParseYaml(trimmed);
  if (yamlParsed !== null) {
    return { parsed: yamlParsed };
  }

  // Strategy 3: Try JSON parse
  const jsonParsed = tryParseJson(trimmed);
  if (jsonParsed !== null) {
    return { parsed: jsonParsed };
  }

  return {
    parseError:
      "LLM output does not contain valid YAML or JSON. " +
      "No fenced code blocks found and content could not be parsed directly.",
  };
}

// ── Fenced code block extraction ──

/**
 * Extract content from the first fenced code block.
 * Supports ```yaml, ```yml, and untyped ``` fences.
 * Returns the extracted content or null if no fence found.
 */
export function extractFencedCodeBlock(text: string): string | null {
  // Match ```yaml, ```yml, or ``` (with optional whitespace after lang tag)
  const fenceRegex = /```(?:ya?ml)?\s*\n([\s\S]*?)```/i;
  const match = text.match(fenceRegex);
  if (match) {
    return match[1].trim();
  }
  return null;
}

// ── Parse helpers ──

/** Try to parse a string as YAML. Returns null on failure. */
function tryParseYaml(text: string): unknown | null {
  try {
    const result = parseYaml(text);
    // yaml library returns undefined for empty input, null for content-only comments
    if (result === undefined || result === null) {
      return null;
    }
    // Must be an object (workflow specs are always objects, not primitives)
    if (typeof result !== "object" || Array.isArray(result)) {
      return null;
    }
    return result;
  } catch {
    return null;
  }
}

/** Try to parse a string as JSON. Returns null on failure. */
function tryParseJson(text: string): unknown | null {
  // Quick check: JSON objects start with {
  if (!text.startsWith("{")) {
    return null;
  }
  try {
    const result = JSON.parse(text);
    if (typeof result !== "object" || result === null || Array.isArray(result)) {
      return null;
    }
    return result;
  } catch {
    return null;
  }
}