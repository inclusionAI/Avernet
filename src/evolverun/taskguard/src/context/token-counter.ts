/**
 * Lightweight token estimation and content classification for context compression.
 *
 * Reuses the CJK/ASCII heuristic from token-usage.ts for consistent estimation
 * without external dependencies (no tiktoken required).
 *
 * @module context/token-counter
 */

import type { ContentSegment, ContentType, ContextEntry, ContextPriority, DependencyClassification } from "./types.js";
import { PRIORITY_ORDER } from "./types.js";

// ── Token estimation ──

/**
 * Estimate token count for a plain text string using CJK/ASCII heuristic.
 * Ported from token-usage.ts for reuse without circular dependency.
 *
 * - CJK characters: ~1.5 chars per token
 * - ASCII characters: ~4 chars per token
 * - Other characters: ~2 chars per token
 */
export function estimateTextTokens(text: string): number {
  if (!text) return 0;
  let cjk = 0;
  let ascii = 0;
  let other = 0;
  for (const char of text) {
    if (/[㐀-鿿豈-﫿]/u.test(char)) {
      cjk += 1;
    } else if (/[\x00-\x7f]/u.test(char)) {
      ascii += 1;
    } else {
      other += 1;
    }
  }
  return Math.ceil(cjk / 1.5 + ascii / 4 + other / 2);
}

/**
 * Estimate token count for a JSON-serializable value.
 * Serializes to string first, then applies text token estimation.
 */
export function estimateJsonTokens(value: unknown): number {
  if (value === undefined) return 0;
  try {
    const text = typeof value === "string" ? value : JSON.stringify(value);
    return estimateTextTokens(text);
  } catch {
    return 0;
  }
}

/**
 * Estimate total token count for a node output map.
 */
export function estimateNodeOutputTokens(
  nodeOutput: Record<string, Record<string, unknown>>,
): number {
  let total = 0;
  for (const output of Object.values(nodeOutput)) {
    total += estimateJsonTokens(output);
  }
  return total;
}

/**
 * Estimate token count for a full WorkflowContext-like object.
 * Counts only the major stringifiable fields.
 */
export function estimateContextTokens(context: Record<string, unknown>): number {
  let total = 0;
  for (const [key, value] of Object.entries(context)) {
    // Metadata fields are small; skip fine-grained counting for them
    if (key === "workflowId" || key === "flowId" || key === "nodeId" || key === "nodeTitle" || key === "history") {
      total += estimateJsonTokens(value);
    } else {
      total += estimateJsonTokens(value);
    }
  }
  return total;
}

// ── Content classification ──

/** Heuristic patterns that indicate error content. */
const ERROR_PATTERNS = [
  /Traceback \(most recent call last\)/i,
  /Error:/i,
  /Exception:/i,
  /FATAL:/i,
  /panic:/i,
  /java\.lang\./,
  /stack trace/i,
  /\"error\"\s*:/i,
  /\"statusCode\"\s*:\s*[45]\d{2}/,
];

/** Heuristic patterns that indicate code content. */
const CODE_PATTERNS = [
  /```[\s\S]*?```/,      // fenced code blocks
  /\n\s*(function|def|class|import|from|export|const|let|var|fn|pub fn|func|package)\s/,  // code keywords on newlines
  /\n\s*(if|for|while|switch|return)\s*[\(\{]/,  // control flow
];

/**
 * Classify the content type of a node output for compressor routing.
 *
 * - "error": Output contains error indicators (stack traces, error objects)
 * - "code": Output contains code (fenced blocks, language keywords)
 * - "data": Output is structured data (mostly primitive values, arrays of objects)
 * - "prose": Output is natural language text
 * - "mixed": Output contains multiple content types
 */
export function classifyContent(output: Record<string, unknown>): ContentType {
  const text = JSON.stringify(output);
  if (!text || text.length < 10) return "data";

  const hasError = ERROR_PATTERNS.some((p) => p.test(text));
  const hasCode = CODE_PATTERNS.some((p) => p.test(text));

  // Check for structured data pattern: object with mostly primitive values
  const values = Object.values(output);
  const primitiveCount = values.filter(
    (v) => v === null || v === undefined || typeof v === "string" || typeof v === "number" || typeof v === "boolean",
  ).length;
  const isMostlyData = values.length > 0 && primitiveCount / values.length >= 0.7;

  if (hasError && hasCode) return "mixed";
  if (hasError) return "error";
  if (hasCode) return "code";
  if (isMostlyData) return "data";
  return "prose";
}

// ── Context entry building ──

/**
 * Build ContextEntry objects from dependency classifications and raw outputs.
 *
 * @param classifications - Dependency classifications with priority and depth
 * @param nodeOutput - Raw node output map (nodeId → output data)
 * @returns Array of context entries ready for the compression pipeline
 */
export function buildContextEntries(
  classifications: DependencyClassification[],
  nodeOutput: Record<string, Record<string, unknown>>,
): ContextEntry[] {
  const entries: ContextEntry[] = [];
  for (const cls of classifications) {
    const output = nodeOutput[cls.nodeId];
    if (!output) continue;
    entries.push({
      nodeId: cls.nodeId,
      priority: cls.priority,
      depth: cls.depth,
      output,
      tokenCount: estimateJsonTokens(output),
      contentType: classifyContent(output),
      compressed: false,
    });
  }
  return entries;
}

/**
 * Sort context entries by priority (ascending: system first, ephemeral last).
 * Within the same priority level, entries with lower depth (closer dependencies) come first.
 */
export function sortByPriority(entries: ContextEntry[]): ContextEntry[] {
  return [...entries].sort((a, b) => {
    const pa = PRIORITY_ORDER[a.priority];
    const pb = PRIORITY_ORDER[b.priority];
    if (pa !== pb) return pa - pb;
    return a.depth - b.depth;
  });
}

/**
 * Calculate total token count for a set of context entries.
 */
export function totalTokens(entries: readonly ContextEntry[]): number {
  return entries.reduce((sum, e) => sum + e.tokenCount, 0);
}

// ── Code-aware content splitting ──

/** Pattern matching a fenced code block (```...```). */
const FENCED_CODE_RE = /```[\s\S]*?```/g;

/** Pattern matching inline code (`...`). */
const INLINE_CODE_RE = /`[^`\n]+`/g;

/** Pattern matching a JSON-like structure (object or array at top level). */
const JSON_BLOCK_RE = /^\s*[\[{]/;

/**
 * Split a text string into content segments classified as code, data, or prose.
 *
 * - **Code segments**: Fenced code blocks (```...```) are preserved verbatim.
 * - **Data segments**: Top-level JSON objects/arrays that span most of the text
 *   are preserved verbatim.
 * - **Prose segments**: Everything else is compressible text.
 *
 * This enables compressors to apply scoring only to prose while keeping
 * code and structured data intact.
 *
 * @param text - The text to split into segments
 * @returns Array of content segments with kind, text, and token count
 */
export function splitContentSegments(text: string): ContentSegment[] {
  if (!text || text.trim().length === 0) return [];

  const segments: ContentSegment[] = [];

  // Extract fenced code blocks first
  const codeBlocks: Array<{ start: number; end: number; text: string }> = [];
  let match: RegExpExecArray | null;
  FENCED_CODE_RE.lastIndex = 0;
  while ((match = FENCED_CODE_RE.exec(text)) !== null) {
    codeBlocks.push({ start: match.index, end: match.index + match[0].length, text: match[0] });
  }

  // If the entire text is a single JSON object/array, return as a data segment
  const trimmed = text.trim();
  if (JSON_BLOCK_RE.test(trimmed)) {
    try {
      JSON.parse(trimmed);
      return [{ kind: "data", text, tokenCount: estimateTextTokens(text) }];
    } catch {
      // Not valid JSON, continue with prose/code splitting
    }
  }

  // If no code blocks, everything is prose (unless it's JSON-like)
  if (codeBlocks.length === 0) {
    return [{ kind: "prose", text, tokenCount: estimateTextTokens(text) }];
  }

  // Interleave code blocks and prose segments
  let lastEnd = 0;
  for (const block of codeBlocks) {
    // Prose before this code block
    if (block.start > lastEnd) {
      const proseText = text.slice(lastEnd, block.start).trim();
      if (proseText.length > 0) {
        segments.push({ kind: "prose", text: proseText, tokenCount: estimateTextTokens(proseText) });
      }
    }
    // The code block itself
    segments.push({ kind: "code", text: block.text, tokenCount: estimateTextTokens(block.text) });
    lastEnd = block.end;
  }

  // Trailing prose after the last code block
  if (lastEnd < text.length) {
    const proseText = text.slice(lastEnd).trim();
    if (proseText.length > 0) {
      segments.push({ kind: "prose", text: proseText, tokenCount: estimateTextTokens(proseText) });
    }
  }

  return segments.length > 0 ? segments : [{ kind: "prose", text, tokenCount: estimateTextTokens(text) }];
}

/**
 * Split a node output (JSON object) into content segments.
 * String values that contain multi-line text are split into segments.
 * Non-string values are treated as data segments.
 *
 * @param output - The node output object to split
 * @returns Array of content segments from all values
 */
export function splitNodeOutputSegments(
  output: Record<string, unknown>,
): ContentSegment[] {
  const segments: ContentSegment[] = [];
  for (const [key, value] of Object.entries(output)) {
    if (typeof value === "string" && value.includes("\n")) {
      // Multi-line string values may contain code blocks
      const subSegments = splitContentSegments(value);
      for (const seg of subSegments) {
        segments.push({ ...seg, text: `[${key}]: ${seg.text}` });
      }
    } else if (typeof value === "string") {
      const text = `[${key}]: ${value}`;
      segments.push({ kind: "prose", text, tokenCount: estimateTextTokens(text) });
    } else {
      const text = `[${key}]: ${JSON.stringify(value)}`;
      segments.push({ kind: "data", text, tokenCount: estimateTextTokens(text) });
    }
  }
  return segments;
}

/**
 * Reassemble content segments back into a single text string.
 */
export function reassembleSegments(segments: readonly ContentSegment[]): string {
  return segments.map((s) => s.text).join("\n");
}