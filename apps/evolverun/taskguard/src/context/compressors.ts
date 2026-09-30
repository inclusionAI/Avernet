/**
 * Pluggable compression strategies for context management.
 *
 * Each compressor implements the ContextCompressor interface and transforms
 * an array of ContextEntry objects in-place (returning new arrays — no mutation).
 *
 * Strategies are ordered by cost:
 *   1. dedup (zero cost, hash-based)
 *   2. error-purge (zero cost, heuristic)
 *   3. priority-evict (zero cost, removes low-priority entries)
 *   4. truncate (zero cost, shortens individual entries)
 *   5. key-value-extract (zero cost, simplifies JSON structure)
 *   6. llm-summarize (premium cost, requires LLM call — NOT implemented here)
 *
 * @module context/compressors
 */

import { createHash } from "node:crypto";
import type { CompressionStrategy, ContextEntry } from "./types.js";
import { PRIORITY_ORDER } from "./types.js";
import { estimateJsonTokens, estimateTextTokens, sortByPriority, splitContentSegments, reassembleSegments } from "./token-counter.js";

// ── Compressor interface ──

/**
 * A compression strategy that transforms context entries.
 * Implementations MUST NOT mutate the input array — return new arrays.
 */
export interface ContextCompressor {
  readonly name: CompressionStrategy;
  compress(entries: ContextEntry[], params?: Record<string, unknown>): ContextEntry[];
}

// ── Dedup ──

/**
 * Hash-based deduplication of identical node outputs.
 * Keeps the highest-priority entry when duplicates are found.
 */
export class DedupCompressor implements ContextCompressor {
  readonly name = "dedup" as const;

  compress(entries: ContextEntry[], _params?: Record<string, unknown>): ContextEntry[] {
    const seen = new Map<string, ContextEntry>();

    for (const entry of entries) {
      const hash = contentHash(entry.output);
      const existing = seen.get(hash);
      if (!existing) {
        seen.set(hash, entry);
        continue;
      }
      // Keep the higher-priority (lower number) entry
      if (PRIORITY_ORDER[entry.priority] < PRIORITY_ORDER[existing.priority]) {
        seen.set(hash, entry);
      } else if (
        PRIORITY_ORDER[entry.priority] === PRIORITY_ORDER[existing.priority]
        && entry.depth < existing.depth
      ) {
        seen.set(hash, entry);
      }
    }

    // Preserve original order for non-deduplicated entries, mark deduplicated ones
    const result: ContextEntry[] = [];
    const usedHashes = new Set<string>();

    for (const entry of entries) {
      const hash = contentHash(entry.output);
      const kept = seen.get(hash);
      if (kept === entry) {
        result.push(entry);
        usedHashes.add(hash);
      } else if (!usedHashes.has(hash)) {
        // This shouldn't happen, but handle gracefully
        result.push({ ...entry, deduplicatedFrom: kept?.nodeId });
        usedHashes.add(hash);
      } else {
        // Entry was deduplicated — skip it
        result.push({
          ...entry,
          output: { _deduplicated: true, _sameAs: kept!.nodeId, _originalSize: JSON.stringify(entry.output).length },
          tokenCount: estimateJsonTokens({ _deduplicated: true, _sameAs: kept!.nodeId }),
          compressed: true,
          deduplicatedFrom: kept!.nodeId,
        });
      }
    }

    return result;
  }
}

// ── Error Purge ──

/**
 * Replace error outputs older than N turns with a compact placeholder.
 * "Turns" is approximated by the entry's depth (deeper = older).
 *
 * Params:
 *   maxAgeTurns: number (default 2) — purge error entries at depth > maxAgeTurns
 */
export class ErrorPurgeCompressor implements ContextCompressor {
  readonly name = "error-purge" as const;

  compress(entries: ContextEntry[], params?: Record<string, unknown>): ContextEntry[] {
    const maxAgeTurns = (params?.maxAgeTurns as number) ?? 2;
    return entries.map((entry) => {
      if (entry.contentType !== "error") return entry;
      if (entry.depth <= maxAgeTurns) return entry;

      const originalSize = JSON.stringify(entry.output).length;
      return {
        ...entry,
        output: {
          _purged: true,
          _reason: "error-purge",
          _originalSize: originalSize,
          _originalNodeId: entry.nodeId,
        },
        tokenCount: estimateJsonTokens({ _purged: true, _reason: "error-purge" }),
        compressed: true,
      };
    });
  }
}

// ── Truncate ──

/**
 * Truncate individual node outputs that exceed a character limit.
 * Ensures valid JSON by truncating the serialized string and closing braces.
 *
 * Params:
 *   maxChars: number (default 2000) — maximum characters per node output
 */
export class TruncateCompressor implements ContextCompressor {
  readonly name = "truncate" as const;

  compress(entries: ContextEntry[], params?: Record<string, unknown>): ContextEntry[] {
    const maxChars = (params?.maxChars as number) ?? 2000;
    return entries.map((entry) => {
      const serialized = JSON.stringify(entry.output, null, 2);
      if (serialized.length <= maxChars) return entry;

      // Truncate and attempt to close the JSON
      const truncated = safeTruncateJson(serialized, maxChars);
      const originalSize = serialized.length;

      try {
        const parsed = JSON.parse(truncated) as Record<string, unknown>;
        return {
          ...entry,
          output: {
            ...parsed,
            _truncated: true,
            _originalSize: originalSize,
          },
          tokenCount: estimateJsonTokens(parsed),
          compressed: true,
        };
      } catch {
        // Fallback: store as a string representation
        return {
          ...entry,
          output: {
            _truncated: true,
            _originalSize: originalSize,
            _preview: truncated.slice(0, maxChars),
          },
          tokenCount: estimateJsonTokens({ _truncated: true, _preview: truncated.slice(0, maxChars) }),
          compressed: true,
        };
      }
    });
  }
}

// ── Priority Evict ──

/**
 * Evict lowest-priority entries to meet a token budget.
 * System-priority entries are never evicted.
 *
 * Params:
 *   maxTokens: number (required) — target token budget
 *   preserveSystem: boolean (default true) — never evict system entries
 */
export class PriorityEvictCompressor implements ContextCompressor {
  readonly name = "priority-evict" as const;

  compress(entries: ContextEntry[], params?: Record<string, unknown>): ContextEntry[] {
    const maxTokens = (params?.maxTokens as number) ?? Infinity;
    const preserveSystem = (params?.preserveSystem as boolean) ?? true;

    // Sort by priority (ascending: system first, ephemeral last)
    const sorted = sortByPriority(entries);
    let currentTokens = entries.reduce((sum, e) => sum + e.tokenCount, 0);

    if (currentTokens <= maxTokens) return entries;

    const evicted = new Set<string>();
    // Evict from lowest priority (ephemeral) to highest
    const reverseSorted = [...sorted].reverse();

    for (const entry of reverseSorted) {
      if (currentTokens <= maxTokens) break;
      if (preserveSystem && entry.priority === "system") continue;
      if (entry.priority === "critical") continue; // Never evict critical either
      currentTokens -= entry.tokenCount;
      evicted.add(entry.nodeId);
    }

    return entries.filter((e) => !evicted.has(e.nodeId));
  }
}

// ── Key-Value Extract ──

/**
 * Simplify verbose JSON outputs by extracting top-level keys with type annotations.
 * For nested objects, replaces the value with a type annotation like "object(3 keys)".
 *
 * Params:
 *   maxKeys: number (default 20) — maximum top-level keys to keep
 *   maxStringLength: number (default 100) — max string length before truncation
 */
export class KeyValueExtractCompressor implements ContextCompressor {
  readonly name = "key-value-extract" as const;

  compress(entries: ContextEntry[], params?: Record<string, unknown>): ContextEntry[] {
    const maxKeys = (params?.maxKeys as number) ?? 20;
    const maxStringLength = (params?.maxStringLength as number) ?? 100;
    return entries.map((entry) => {
      // Only extract from entries that have complex JSON structure
      if (entry.contentType === "code" || entry.contentType === "error") return entry;

      const extracted = extractKeyValueSummary(entry.output, maxKeys, maxStringLength);
      const newTokenCount = estimateJsonTokens(extracted);

      // Only apply if it actually reduced size meaningfully (>20% reduction)
      if (newTokenCount >= entry.tokenCount * 0.8) return entry;

      return {
        ...entry,
        output: {
          ...extracted,
          _extracted: true,
          _originalTokenCount: entry.tokenCount,
        },
        tokenCount: newTokenCount,
        compressed: true,
      };
    });
  }
}

// ── Verbatim (no-op) ──

/** No-op compressor that passes entries through unchanged. */
export class VerbatimCompressor implements ContextCompressor {
  readonly name = "verbatim" as const;
  compress(entries: ContextEntry[]): ContextEntry[] {
    return entries;
  }
}

// ── Utility functions ──

/** Compute a fast content hash for dedup. */
function contentHash(output: Record<string, unknown>): string {
  const serialized = JSON.stringify(output);
  return createHash("sha256").update(serialized).digest("hex").slice(0, 16);
}

/**
 * Safely truncate a JSON string and attempt to close brackets.
 * Used by TruncateCompressor.
 */
function safeTruncateJson(json: string, maxChars: number): string {
  if (json.length <= maxChars) return json;

  let truncated = json.slice(0, maxChars);

  // Count open vs close brackets/braces
  const openBrackets = (truncated.match(/\[/g) ?? []).length;
  const closeBrackets = (truncated.match(/\]/g) ?? []).length;
  const openBraces = (truncated.match(/\{/g) ?? []).length;
  const closeBraces = (truncated.match(/\}/g) ?? []).length;

  // Close any unclosed brackets/braces
  truncated += "]".repeat(Math.max(0, openBrackets - closeBrackets));
  truncated += "}".repeat(Math.max(0, openBraces - closeBraces));

  return truncated;
}

/**
 * Extract a key-value summary from a verbose JSON output.
 * Primitive values are kept as-is; complex values get type annotations.
 */
function extractKeyValueSummary(
  output: Record<string, unknown>,
  maxKeys: number,
  maxStringLength: number,
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  const keys = Object.keys(output).slice(0, maxKeys);

  for (const key of keys) {
    const value = output[key];
    result[key] = summarizeValue(value, maxStringLength);
  }

  if (Object.keys(output).length > maxKeys) {
    result._additionalKeys = Object.keys(output).length - maxKeys;
  }

  return result;
}

/**
 * Summarize a single value for key-value extraction.
 */
function summarizeValue(value: unknown, maxStringLength: number): unknown {
  if (value === null) return null;
  if (value === undefined) return undefined;

  if (typeof value === "string") {
    return value.length > maxStringLength
      ? `${value.slice(0, maxStringLength)}... (${value.length} chars)`
      : value;
  }

  if (typeof value === "number" || typeof value === "boolean") return value;

  if (Array.isArray(value)) {
    if (value.length === 0) return "[](empty)";
    if (value.length <= 3 && value.every(isPrimitive)) return value;
    return `Array(${value.length} items)`;
  }

  if (typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>);
    if (keys.length === 0) return "{}(empty)";
    return `Object(${keys.length} keys: ${keys.slice(0, 5).join(", ")}${keys.length > 5 ? "..." : ""})`;
  }

  return String(value);
}

function isPrimitive(value: unknown): boolean {
  return value === null || value === undefined || typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}

// ── Sentence Score + Budget Packing ──

/**
 * Information-density signal patterns for sentence scoring.
 * Sentences matching these patterns receive a score boost because
 * they carry high-density technical information.
 */
const HIGH_VALUE_PATTERNS = [
  /\b(id|ID|uuid|key|token|hash|version|sha|commit)\s*[:=]\s*\S+/i,   // Identifiers
  /\b\d+\.?\d*\s*%/i,                                                     // Percentages
  /\b(error|fail|success|passed|warning|critical)\s*[:=]\s*/i,            // Status indicators
  /\b(return|result|output|response|status)\s*[:=]\s*\{/i,                // Structured results
  /\b(AWS|GCP|Azure|API|HTTP|URL|endpoint|host|port|region)\s*[:=]\s*/i, // Infrastructure
  /\b(true|false|null|undefined|0x[0-9a-f]+)\b/i,                         // Literal values
];

/**
 * Low-value signal patterns that indicate filler or boilerplate text.
 * Sentences matching these patterns receive a score penalty.
 */
const LOW_VALUE_PATTERNS = [
  /^(please|note that|it is worth|as you can see|in summary|in conclusion)/i,
  /\b(obviously|clearly|basically|essentially|simply put|in other words)\b/i,
  /^(the following|this section|this document|this report)\b/i,
];

/**
 * A scored sentence ready for budget packing.
 */
type ScoredSentence = {
  /** The sentence text. */
  text: string;
  /** Information density score (0–1, higher = more valuable). */
  score: number;
  /** Estimated token count. */
  tokenCount: number;
  /** Index in the original sentence list (for stable ordering). */
  index: number;
};

/**
 * Score a single sentence by information density.
 *
 * Scoring factors:
 * - Length: very short (<20 chars) or very long (>500 chars) sentences are penalized
 * - Identifiers and values: sentences with IDs, numbers, keys get a boost
 * - Filler phrases: sentences starting with "please", "note that" etc. get a penalty
 * - Code indicators: sentences with brackets, equals, semicolons get a boost
 *
 * @returns Score between 0 and 1 (higher = more valuable)
 */
function scoreSentence(sentence: string): number {
  const trimmed = sentence.trim();
  if (trimmed.length === 0) return 0;

  let score = 0.5; // baseline

  // Length penalty: very short sentences are usually headers/fillers
  if (trimmed.length < 20) score -= 0.15;
  else if (trimmed.length < 40) score -= 0.05;
  // Very long sentences may be run-on; slight penalty
  if (trimmed.length > 500) score -= 0.1;

  // High-value pattern boosts
  for (const pattern of HIGH_VALUE_PATTERNS) {
    if (pattern.test(trimmed)) {
      score += 0.2;
      break; // Only apply once per category
    }
  }

  // Low-value pattern penalties
  for (const pattern of LOW_VALUE_PATTERNS) {
    if (pattern.test(trimmed)) {
      score -= 0.2;
      break;
    }
  }

  // Code/structure indicators
  const codeSignals = (trimmed.match(/[{}[\]=;:]/g) ?? []).length;
  if (codeSignals > 3) score += 0.1;

  // Contains numbers (quantitative info)
  if (/\d+/.test(trimmed)) score += 0.05;

  return Math.max(0, Math.min(1, score));
}

/**
 * Split text into sentences for scoring.
 * Uses a simple heuristic: split on sentence-ending punctuation
 * followed by whitespace and a capital letter, or on double newlines.
 */
function splitIntoSentences(text: string): string[] {
  // Split on double newlines (paragraph breaks)
  const paragraphs = text.split(/\n\s*\n/);

  const sentences: string[] = [];
  for (const para of paragraphs) {
    // Split on sentence boundaries: period/question/exclamation + space + capital
    const parts = para.split(/(?<=[.!?])\s+(?=[A-Z一-鿿])/);
    for (const part of parts) {
      const trimmed = part.trim();
      if (trimmed.length > 0) {
        sentences.push(trimmed);
      }
    }
  }

  return sentences;
}

/**
 * Budget packing: select highest-value sentences that fit within a token budget.
 *
 * Uses a greedy knapsack approach: sentences are sorted by score/token efficiency,
 * then selected in order until the budget is exhausted.
 *
 * @param sentences - Scored sentences to pack
 * @param budget - Maximum token count
 * @returns Selected sentences in original document order
 */
function packBudget(sentences: ScoredSentence[], budget: number): ScoredSentence[] {
  if (sentences.length === 0) return [];
  if (budget <= 0) return [];

  // Sort by score-per-token efficiency (descending)
  const sorted = [...sentences]
    .map((s) => ({ ...s, efficiency: s.tokenCount > 0 ? s.score / s.tokenCount : 0 }))
    .sort((a, b) => b.efficiency - a.efficiency);

  const selected: ScoredSentence[] = [];
  let usedTokens = 0;

  for (const sentence of sorted) {
    if (usedTokens + sentence.tokenCount <= budget) {
      selected.push(sentence);
      usedTokens += sentence.tokenCount;
    }
    // Skip sentences that don't fit (no partial selection)
  }

  // Return in original document order for coherence
  return selected.sort((a, b) => a.index - b.index);
}

/**
 * Sentence scoring + budget packing compressor.
 *
 * Splits node output text into sentences, scores each by information density,
 * and packs the highest-value sentences into a token budget.
 *
 * Code blocks, JSON structures, and data segments are preserved verbatim —
 * only prose segments are scored and potentially trimmed.
 *
 * Params:
 *   maxTokens: number (default: 2000) — target token budget per entry
 *   minSentences: number (default: 3) — always keep at least this many sentences
 */
export class SentenceScoreCompressor implements ContextCompressor {
  readonly name = "sentence-score" as const;

  compress(entries: ContextEntry[], params?: Record<string, unknown>): ContextEntry[] {
    const maxTokens = (params?.maxTokens as number) ?? 2000;
    const minSentences = (params?.minSentences as number) ?? 3;

    return entries.map((entry) => {
      const serialized = JSON.stringify(entry.output, null, 2);
      if (estimateTextTokens(serialized) <= maxTokens) return entry;

      // Only apply sentence scoring to prose and mixed content
      if (entry.contentType !== "prose" && entry.contentType !== "mixed") return entry;

      return this.compressEntry(entry, maxTokens, minSentences);
    });
  }

  private compressEntry(
    entry: ContextEntry,
    maxTokens: number,
    minSentences: number,
  ): ContextEntry {
    // Serialize the output to text for segment splitting
    const serialized = JSON.stringify(entry.output, null, 2);
    const segments = splitContentSegments(serialized);

    // Separate verbatim (code/data) from compressible (prose) segments
    const verbatimSegments = segments.filter((s) => s.kind !== "prose");
    const proseSegments = segments.filter((s) => s.kind === "prose");

    // Calculate remaining budget after verbatim segments
    const verbatimTokens = verbatimSegments.reduce((sum, s) => sum + s.tokenCount, 0);
    const proseBudget = Math.max(0, maxTokens - verbatimTokens);

    if (proseBudget <= 0) {
      // Verbatim content alone exceeds budget — fall through to truncation
      return entry;
    }

    // Split prose into sentences and score them
    const allSentences: ScoredSentence[] = [];
    for (const seg of proseSegments) {
      const sentences = splitIntoSentences(seg.text);
      for (let i = 0; i < sentences.length; i++) {
        const text = sentences[i];
        allSentences.push({
          text,
          score: scoreSentence(text),
          tokenCount: estimateTextTokens(text),
          index: allSentences.length + i,
        });
      }
    }

    if (allSentences.length <= minSentences) {
      // Too few sentences to compress meaningfully
      return entry;
    }

    // Pack the best sentences into budget
    const packed = packBudget(allSentences, proseBudget);

    // Ensure we keep at least minSentences (pick the top-scored ones)
    if (packed.length < minSentences && allSentences.length >= minSentences) {
      const topScored = [...allSentences]
        .sort((a, b) => b.score - a.score)
        .slice(0, minSentences);
      const packedSet = new Set(packed.map((s) => s.index));
      for (const s of topScored) {
        if (!packedSet.has(s.index)) {
          packed.push(s);
        }
      }
      // Re-sort by document order
      packed.sort((a, b) => a.index - b.index);
    }

    // Reassemble: verbatim segments + selected prose sentences
    const selectedProseText = packed.map((s) => s.text).join("\n\n");
    const compressedSegments = [
      ...verbatimSegments,
      { kind: "prose" as const, text: selectedProseText, tokenCount: estimateTextTokens(selectedProseText) },
    ].sort((a, b) => {
      // Keep code before prose (approximate original order)
      if (a.kind === "code" && b.kind !== "code") return -1;
      if (a.kind !== "code" && b.kind === "code") return 1;
      return 0;
    });

    const compressedText = reassembleSegments(compressedSegments);

    // Try to parse the result back as JSON; if it fails, wrap it
    let compressedOutput: Record<string, unknown>;
    try {
      const parsed = JSON.parse(compressedText);
      if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
        compressedOutput = parsed as Record<string, unknown>;
      } else {
        compressedOutput = { _compressed: true, _summary: compressedText };
      }
    } catch {
      compressedOutput = { _compressed: true, _summary: compressedText };
    }

    const newTokenCount = estimateJsonTokens(compressedOutput);
    const originalSize = JSON.stringify(entry.output).length;

    return {
      ...entry,
      output: {
        ...compressedOutput,
        _scoreCompressed: true,
        _originalTokenCount: entry.tokenCount,
        _originalSize: originalSize,
        _sentencesTotal: allSentences.length,
        _sentencesKept: packed.length,
      },
      tokenCount: newTokenCount,
      compressed: true,
    };
  }
}

// ── Fuzzy Dedup ──

/**
 * Fuzzy deduplication compressor.
 *
 * Detects near-duplicate entries using Jaccard similarity on token sets.
 * When similarity between two entries exceeds a threshold, the lower-priority
 * entry is replaced with a compact reference to the higher-priority one.
 *
 * This complements the exact hash-based DedupCompressor by catching
 * entries that are substantively identical but differ in formatting,
 * timestamps, or minor whitespace differences.
 *
 * Params:
 *   similarityThreshold: number (default 0.8) — Jaccard similarity threshold (0–1)
 *   minTokens: number (default 20) — skip entries smaller than this
 */
export class FuzzyDedupCompressor implements ContextCompressor {
  readonly name = "fuzzy-dedup" as const;

  compress(entries: ContextEntry[], params?: Record<string, unknown>): ContextEntry[] {
    const threshold = (params?.similarityThreshold as number) ?? 0.8;
    const minTokens = (params?.minTokens as number) ?? 20;

    if (entries.length <= 1) return entries;

    // Pre-compute token sets for all entries
    const tokenSets = entries.map((entry) => {
      const text = JSON.stringify(entry.output).toLowerCase();
      return tokenize(text);
    });

    const deduplicated = new Set<number>(); // indices of entries replaced
    const references = new Map<number, number>(); // index → reference index

    for (let i = 0; i < entries.length; i++) {
      if (deduplicated.has(i)) continue;
      if (entries[i].tokenCount < minTokens) continue;

      for (let j = i + 1; j < entries.length; j++) {
        if (deduplicated.has(j)) continue;
        if (entries[j].tokenCount < minTokens) continue;

        const similarity = jaccardSimilarity(tokenSets[i], tokenSets[j]);
        if (similarity >= threshold) {
          // Keep the higher-priority entry (lower PRIORITY_ORDER number)
          const iPrio = PRIORITY_ORDER[entries[i].priority];
          const jPrio = PRIORITY_ORDER[entries[j].priority];

          if (jPrio >= iPrio) {
            // j is lower or equal priority — replace j with reference to i
            deduplicated.add(j);
            references.set(j, i);
          } else {
            // i is lower priority — replace i with reference to j
            deduplicated.add(i);
            references.set(i, j);
            break; // i is now deduplicated, stop comparing from it
          }
        }
      }
    }

    return entries.map((entry, idx) => {
      if (!deduplicated.has(idx)) return entry;

      const refIdx = references.get(idx)!;
      const refEntry = entries[refIdx]!;
      const originalSize = JSON.stringify(entry.output).length;

      return {
        ...entry,
        output: {
          _fuzzyDeduplicated: true,
          _similarTo: refEntry.nodeId,
          _similarityNote: `Highly similar to output from "${refEntry.nodeId}"`,
          _originalSize: originalSize,
        },
        tokenCount: estimateJsonTokens({ _fuzzyDeduplicated: true, _similarTo: refEntry.nodeId }),
        compressed: true,
        deduplicatedFrom: refEntry.nodeId,
      };
    });
  }
}

/**
 * Tokenize a string into a set of lowercase tokens for Jaccard similarity.
 * Splits on non-alphanumeric characters and filters out very short tokens.
 */
function tokenize(text: string): Set<string> {
  const tokens = text
    .split(/[^a-z0-9一-鿿]+/)
    .filter((t) => t.length >= 2);
  return new Set(tokens);
}

/**
 * Compute Jaccard similarity between two sets.
 * J(A, B) = |A ∩ B| / |A ∪ B|
 */
function jaccardSimilarity(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 && b.size === 0) return 1;
  if (a.size === 0 || b.size === 0) return 0;

  let intersection = 0;
  for (const item of a) {
    if (b.has(item)) intersection++;
  }

  const union = a.size + b.size - intersection;
  return union === 0 ? 0 : intersection / union;
}

// ── Compressor registry ──

/** Map of strategy name to compressor class. */
const COMPRESSOR_REGISTRY: Record<CompressionStrategy, () => ContextCompressor> = {
  verbatim: () => new VerbatimCompressor(),
  dedup: () => new DedupCompressor(),
  "fuzzy-dedup": () => new FuzzyDedupCompressor(),
  "error-purge": () => new ErrorPurgeCompressor(),
  truncate: () => new TruncateCompressor(),
  "priority-evict": () => new PriorityEvictCompressor(),
  "key-value-extract": () => new KeyValueExtractCompressor(),
  "sentence-score": () => new SentenceScoreCompressor(),
  "llm-summarize": () => new VerbatimCompressor(), // Placeholder: falls back to verbatim
};

/**
 * Create a compressor instance by strategy name.
 * Unknown strategies fall back to verbatim.
 */
export function createCompressor(strategy: CompressionStrategy): ContextCompressor {
  const factory = COMPRESSOR_REGISTRY[strategy];
  return factory ? factory() : new VerbatimCompressor();
}