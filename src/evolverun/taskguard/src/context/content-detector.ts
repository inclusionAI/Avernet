/**
 * Content-type detection for session message tool results.
 *
 * Inspired by Claw Compactor's Cortex stage: auto-detect the content type
 * of tool output to enable content-aware compression. Detection priority is:
 *   1. Structural markers (JSON root, diff headers, log timestamps)
 *   2. Language heuristics (shebang, indent chars, keyword density)
 *   3. Fallback to "text"
 *
 * Detection is O(k) where k is the number of bytes examined (capped at
 * CONTENT_PREVIEW_BYTES). It does NOT scan the entire output.
 *
 * @module context/content-detector
 */

// ── Types ──

/** Content types detected in tool output, matching Claw Compactor's Cortex. */
export type ToolContentType =
  | "json"    // JSON data (objects, arrays)
  | "log"     // Build/test/runtime logs with timestamps or log levels
  | "diff"    // Unified diff / git diff output
  | "code"    // Source code in any language
  | "search"  // Search results (grep, find, code search)
  | "text";   // Fallback: natural language or unstructured text

/** Result of content type detection. */
export type ContentDetectionResult = {
  /** The detected content type. */
  type: ToolContentType;
  /** Confidence level (0-1). Higher means more certain. */
  confidence: number;
  /** For "json" type, whether the root is an array. */
  isJsonArray?: boolean;
  /** For "json" type, estimated number of elements if array. */
  jsonArrayLength?: number;
  /** For "code" type, detected programming language (best guess). */
  language?: string;
};

// ── Constants ──

/** Maximum bytes to examine for content detection. */
const CONTENT_PREVIEW_BYTES = 4096;

// ── Detection Patterns ──

/** JSON root token at the start of trimmed content. */
const JSON_ROOT_RE = /^\s*[\[{]/;

/** Unified diff hunk header. */
const DIFF_HUNK_RE = /^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@/;

/** Diff file header. */
const DIFF_FILE_RE = /^(?:diff --git|--- |\+\+\+ |\*{15}|Index: )/;

/** Common log timestamp patterns. */
const LOG_TIMESTAMP_RE = /^\s*\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}[:.]\d{2}|^\s*\[\d{4}-\d{2}-\d{2}|^\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}/;

/** Log level markers. */
const LOG_LEVEL_RE = /^\s*(?:TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\s*[\s[:\]]/;

/** Shebang line. */
const SHEBANG_RE = /^#!/;

/** Code indicators (common programming constructs). */
const CODE_INDICATOR_RE = /(?:^|\n)\s*(?:function\s|class\s|def\s|import\s|from\s|const\s|let\s|var\s|return\s|if\s*\(|for\s*\(|while\s*\(|try\s*\{|pub\s+fn|mod\s|use\s|fn\s|impl\s|struct\s|enum\s|interface\s|type\s)/;

/** Search result patterns (file:line or rank markers). */
const SEARCH_RESULT_RE = /^(?:[^\n:]+):\d+:(?:\d+:)?\s/;

/** Grep-like output: file:line:content */
const GREP_OUTPUT_RE = /^[^\n:]+:\d+:.+/m;

// ── Public API ──

/**
 * Detect the content type of a text string (typically tool output).
 *
 * Examines up to CONTENT_PREVIEW_BYTES of the text for structural and
 * lexical patterns. Returns the detected type with a confidence score.
 *
 * Priority order (first match wins):
 * 1. JSON → detects objects and arrays, including array length
 * 2. Diff → unified diff headers
 * 3. Log → timestamps and log levels
 * 4. Search → grep/find/code-search result format
 * 5. Code → shebang, programming constructs, indented blocks
 * 6. Text → fallback
 */
export function detectContentType(text: string): ContentDetectionResult {
  if (!text || text.trim().length === 0) {
    return { type: "text", confidence: 1.0 };
  }

  const preview = text.slice(0, CONTENT_PREVIEW_BYTES);
  const trimmed = preview.trim();

  // 1. JSON detection — highest priority for structured data
  const jsonResult = tryDetectJson(trimmed, text);
  if (jsonResult) return jsonResult;

  // 2. Diff detection
  const diffResult = tryDetectDiff(preview);
  if (diffResult) return diffResult;

  // 3. Log detection
  const logResult = tryDetectLog(preview);
  if (logResult) return logResult;

  // 4. Search result detection
  const searchResult = tryDetectSearch(preview);
  if (searchResult) return searchResult;

  // 5. Code detection
  const codeResult = tryDetectCode(preview);
  if (codeResult) return codeResult;

  // 6. Fallback to text
  return { type: "text", confidence: 0.5 };
}

// ── Detectors ──

function tryDetectJson(trimmed: string, fullText: string): ContentDetectionResult | null {
  if (!JSON_ROOT_RE.test(trimmed)) return null;

  // Try to parse the preview as JSON
  try {
    const parsed = JSON.parse(trimmed);

    if (Array.isArray(parsed)) {
      return {
        type: "json",
        confidence: 0.95,
        isJsonArray: true,
        jsonArrayLength: parsed.length,
      };
    }

    if (typeof parsed === "object" && parsed !== null) {
      // Check if it's a large object that might be wrapping an array
      const keys = Object.keys(parsed);
      const hasDataArray = keys.some((k) =>
        Array.isArray((parsed as Record<string, unknown>)[k]) &&
        ((parsed as Record<string, unknown>)[k] as unknown[]).length > 10,
      );

      if (hasDataArray) {
        return {
          type: "json",
          confidence: 0.85,
          isJsonArray: false,
          // Note: the array is nested, not at root
        };
      }

      return { type: "json", confidence: 0.9, isJsonArray: false };
    }

    return null;
  } catch {
    // Not valid JSON — might be truncated or have trailing content.
    // Check for strong JSON-like indicators.
    const lines = trimmed.split("\n");
    const jsonLikeLines = lines.filter((l) =>
      /^\s*[{"]|^\s*\[|:\s*["{\[\dtnf]/.test(l),
    );
    if (jsonLikeLines.length / lines.length > 0.6) {
      return { type: "json", confidence: 0.6, isJsonArray: trimmed.trimStart().startsWith("[") };
    }
    return null;
  }
}

function tryDetectDiff(preview: string): ContentDetectionResult | null {
  const lines = preview.split("\n");
  let diffIndicators = 0;

  for (const line of lines) {
    if (DIFF_HUNK_RE.test(line) || DIFF_FILE_RE.test(line)) {
      diffIndicators++;
    }
    // Count added/removed lines
    if (/^\+[^+]/.test(line) || /^-[^-]/.test(line)) {
      diffIndicators++;
    }
  }

  if (diffIndicators >= 3) {
    return { type: "diff", confidence: 0.9 };
  }
  if (diffIndicators >= 2 && lines.length > 5) {
    return { type: "diff", confidence: 0.7 };
  }

  return null;
}

function tryDetectLog(preview: string): ContentDetectionResult | null {
  const lines = preview.split("\n").slice(0, 50); // Check first 50 lines
  let logLines = 0;

  for (const line of lines) {
    if (LOG_TIMESTAMP_RE.test(line) || LOG_LEVEL_RE.test(line)) {
      logLines++;
    }
  }

  if (logLines >= 3) {
    return { type: "log", confidence: 0.9 };
  }
  if (logLines >= 2 && lines.length > 10) {
    return { type: "log", confidence: 0.7 };
  }

  return null;
}

function tryDetectSearch(preview: string): ContentDetectionResult | null {
  const lines = preview.split("\n").slice(0, 30);

  let searchLines = 0;
  for (const line of lines) {
    if (SEARCH_RESULT_RE.test(line) || GREP_OUTPUT_RE.test(line)) {
      searchLines++;
    }
  }

  // Also check for common search result headers
  const hasSearchHeader = /(?:found \d+ (?:result|match|file)|search complete|showing \d+ (?:of|result))/i.test(preview.slice(0, 500));

  if (searchLines >= 5 || (searchLines >= 3 && hasSearchHeader)) {
    return { type: "search", confidence: 0.85 };
  }

  return null;
}

function tryDetectCode(preview: string): ContentDetectionResult | null {
  const lines = preview.split("\n");

  // Shebang is a strong indicator
  if (SHEBANG_RE.test(preview.trimStart())) {
    return { type: "code", confidence: 0.9, language: detectLanguage(preview) };
  }

  // Code construct density
  const codeLines = lines.filter((l) => CODE_INDICATOR_RE.test(l)).length;
  const totalLines = lines.length;

  if (totalLines > 5 && codeLines / totalLines > 0.2) {
    return { type: "code", confidence: 0.75, language: detectLanguage(preview) };
  }

  // Indentation patterns (4 spaces or tabs at line start suggest code)
  const indentedLines = lines.filter((l) => /^(?: {4}|\t)/.test(l)).length;
  if (totalLines > 10 && indentedLines / totalLines > 0.4) {
    return { type: "code", confidence: 0.6, language: detectLanguage(preview) };
  }

  return null;
}

/** Detect programming language from code content (best-effort heuristic). */
function detectLanguage(text: string): string {
  const preview = text.slice(0, 2000);

  if (preview.includes("def ") && preview.includes(":") && !preview.includes("//")) return "python";
  if (preview.includes("function ") && preview.includes("=>")) return "typescript";
  if (preview.includes("function ") && preview.includes("{")) return "javascript";
  if (preview.includes("func ") && preview.includes("package ")) return "go";
  if (preview.includes("fn ") && preview.includes("let mut")) return "rust";
  if (preview.includes("class ") && preview.includes("public ")) return "java";
  if (preview.includes("import ") && preview.includes("export default")) return "typescript";
  if (preview.includes("<?php")) return "php";
  if (preview.includes("#include")) return "cpp";
  if (preview.includes("using namespace")) return "cpp";
  if (preview.includes("SELECT ") && preview.includes("FROM ")) return "sql";

  return "unknown";
}