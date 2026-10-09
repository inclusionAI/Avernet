/**
 * LLM-based semantic compression for context management.
 *
 * Uses an OpenAI-compatible chat completions API to summarize verbose
 * node outputs into compact representations while preserving key information.
 *
 * Features:
 * - 3-level escalation strategy (normal → aggressive → deterministic fallback)
 * - Recency window protection (safeguard recent entries from summarization)
 * - Content-type-aware summarization (code and errors are never summarized)
 * - Provenance tracking (original outputs stored for reversible decompression)
 *
 * Falls back gracefully to verbatim output if the LLM call fails.
 * Reuses the same LLM_BASE_URL / LLM_API_KEY / LLM_MODEL env vars
 * as the validation module for consistency.
 *
 * @module context/llm-summarizer
 */

import { createHash } from "node:crypto";
import type { ContextEntry, ContentType, CompressionProvenance } from "./types.js";
import { PRIORITY_ORDER } from "./types.js";
import { estimateJsonTokens, estimateTextTokens } from "./token-counter.js";

// ── Configuration ──

/** Configuration for the LLM summarizer. */
export type LlmSummarizerConfig = {
  /** OpenAI-compatible API base URL. Default: LLM_BASE_URL env var */
  baseUrl?: string;
  /** API key for authentication. Default: LLM_API_KEY env var */
  apiKey?: string;
  /** Model to use for summarization. Default: LLM_MODEL env var or "gpt-4o-mini" */
  model?: string;
  /** Target compression ratio (0–1). Default: 0.3 (compress to ~30% of original) */
  targetRatio?: number;
  /** Maximum tokens for the LLM response. Default: 1024 */
  maxResponseTokens?: number;
  /** Request timeout in milliseconds. Default: 10000 */
  timeoutMs?: number;
  /** Minimum token count for an entry to be considered for summarization. Default: 200 */
  minTokensForSummarization?: number;
  /** Content types eligible for summarization. Default: ["prose", "mixed", "data"] */
  eligibleContentTypes?: ContentType[];
  /** Number of recent entries to protect from summarization. Default: 3 */
  recencyWindow?: number;
  /** Aggressive compression ratio for Level 2 escalation. Default: 0.15 */
  aggressiveRatio?: number;
  /** Whether to enable Level 3 deterministic fallback. Default: true */
  enableDeterministicFallback?: boolean;
};

/** Resolved (non-optional) config with env var fallbacks. */
type ResolvedConfig = {
  baseUrl: string;
  apiKey: string;
  model: string;
  targetRatio: number;
  maxResponseTokens: number;
  timeoutMs: number;
  minTokensForSummarization: number;
  eligibleContentTypes: Set<ContentType>;
  recencyWindow: number;
  aggressiveRatio: number;
  enableDeterministicFallback: boolean;
};

const DEFAULT_ELIGIBLE_TYPES: ContentType[] = ["prose", "mixed", "data"];

function resolveConfig(config?: LlmSummarizerConfig): ResolvedConfig {
  return {
    baseUrl: config?.baseUrl ?? process.env.LLM_BASE_URL ?? "",
    apiKey: config?.apiKey ?? process.env.LLM_API_KEY ?? "",
    model: config?.model ?? process.env.LLM_MODEL ?? "gpt-4o-mini",
    targetRatio: config?.targetRatio ?? 0.3,
    maxResponseTokens: config?.maxResponseTokens ?? 1024,
    timeoutMs: config?.timeoutMs ?? 10000,
    minTokensForSummarization: config?.minTokensForSummarization ?? 200,
    eligibleContentTypes: new Set(config?.eligibleContentTypes ?? DEFAULT_ELIGIBLE_TYPES),
    recencyWindow: config?.recencyWindow ?? 3,
    aggressiveRatio: config?.aggressiveRatio ?? 0.15,
    enableDeterministicFallback: config?.enableDeterministicFallback ?? true,
  };
}

// ── Escalation levels ──

/** Summarization attempt result. */
type SummarizationAttempt = {
  summary: Record<string, unknown> | null;
  level: "normal" | "aggressive" | "deterministic";
  tokensSaved: number;
  error?: string;
};

/**
 * Level 1: Normal summarization prompt.
 * Moderate compression with focus on preserving key information.
 */
const SYSTEM_PROMPT_NORMAL = `You are a context compression assistant for a workflow engine.
Your task is to summarize verbose node outputs into compact, information-dense representations.

Rules:
1. Preserve ALL key facts, decisions, identifiers, and quantitative results.
2. Remove redundant explanations, boilerplate, and verbose descriptions.
3. Keep the output as valid JSON that matches the original structure as closely as possible.
4. For nested objects, preserve key names but summarize values.
5. For arrays, keep the count and summarize representative items.
6. Do NOT add any information that was not in the original.
7. Output ONLY the compressed JSON, no markdown or explanation.`;

/**
 * Level 2: Aggressive summarization prompt.
 * More aggressive compression, removes more detail, keeps only essential information.
 */
const SYSTEM_PROMPT_AGGRESSIVE = `You are an aggressive context compression assistant. Compress the following workflow node output to its bare essentials.

Rules:
1. Keep ONLY: IDs, names, decisions, error messages, numeric results, and status codes.
2. DISCARD: explanations, descriptions, logs, stack traces (keep only the error type), and decorative text.
3. Flatten nested objects — keep keys but replace complex values with type annotations like "Object(3 keys)".
4. Replace arrays of objects with a count: "Array(N items, first: {key fields})".
5. Output valid JSON with the same top-level keys.
6. Output ONLY the compressed JSON, no markdown or explanation.`;

function buildSummarizationPrompt(
  nodeOutput: Record<string, unknown>,
  targetRatio: number,
  nodeId: string,
  level: "normal" | "aggressive",
): string {
  const serialized = JSON.stringify(nodeOutput, null, 2);
  const approxTokens = estimateJsonTokens(nodeOutput);
  const targetTokens = Math.ceil(approxTokens * targetRatio);
  const percentage = Math.round(targetRatio * 100);

  if (level === "aggressive") {
    return `Aggressively compress the following workflow node output (from node "${nodeId}") to approximately ${percentage}% of its original detail (~${targetTokens} tokens target, original ~${approxTokens} tokens).

Strip all non-essential information. Keep only IDs, decisions, errors, and key results.

<content>
${serialized}
</content>

Return only the compressed JSON object.`;
  }

  return `Compress the following workflow node output (from node "${nodeId}") to approximately ${percentage}% of its original detail (~${targetTokens} tokens target, original ~${approxTokens} tokens).

Preserve all critical information: IDs, names, decisions, scores, errors, and quantitative results.
Remove verbosity, repetition, and decorative text.

<content>
${serialized}
</content>

Return only the compressed JSON object.`;
}

// ── LLM API call ──

/** A single message in the chat completions format. */
type ChatMessage = {
  role: "system" | "user" | "assistant";
  content: string;
};

/** Response from the OpenAI-compatible API. */
type ChatCompletionResponse = {
  choices: Array<{ message: { content: string } }>;
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
};

/**
 * Call an OpenAI-compatible chat completions API.
 * Returns the assistant message content and optional usage stats.
 */
async function callChatApi(
  config: ResolvedConfig,
  messages: ChatMessage[],
): Promise<{ content: string; usage?: { inputTokens: number; outputTokens: number } }> {
  if (!config.baseUrl || !config.apiKey) {
    throw new Error("LLM summarizer requires LLM_BASE_URL and LLM_API_KEY to be set");
  }

  const url = `${config.baseUrl.replace(/\/$/, "")}/v1/chat/completions`;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), config.timeoutMs);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${config.apiKey}`,
      },
      body: JSON.stringify({
        model: config.model,
        messages,
        temperature: 0.1,
        max_tokens: config.maxResponseTokens,
      }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const body = await response.text().catch(() => response.statusText);
      throw new Error(`LLM API ${response.status}: ${body.slice(0, 200)}`);
    }

    const data = (await response.json()) as ChatCompletionResponse;
    const content = data.choices?.[0]?.message?.content ?? "";

    return {
      content,
      usage: data.usage
        ? {
            inputTokens: data.usage.prompt_tokens ?? 0,
            outputTokens: data.usage.completion_tokens ?? 0,
          }
        : undefined,
    };
  } finally {
    clearTimeout(timeout);
  }
}

/**
 * Parse LLM response content as JSON, stripping markdown code fences if present.
 * Returns null if parsing fails.
 */
function parseLlmJson(content: string): Record<string, unknown> | null {
  const cleaned = content
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```\s*$/i, "")
    .trim();

  try {
    const parsed = JSON.parse(cleaned);
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
    return null;
  } catch {
    return null;
  }
}

// ── Deterministic fallback (Level 3) ──

/**
 * Level 3: Deterministic key-value extraction fallback.
 * Used when LLM summarization fails or produces output larger than input.
 * Extracts top-level keys and summarizes values aggressively.
 */
function deterministicFallback(
  entry: ContextEntry,
): { summary: Record<string, unknown>; tokensSaved: number } {
  const output = entry.output;
  const result: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(output)) {
    if (key.startsWith("_")) {
      result[key] = value; // Preserve metadata fields
      continue;
    }

    if (value === null || value === undefined) {
      result[key] = value;
    } else if (typeof value === "string") {
      result[key] = value.length > 100 ? `${value.slice(0, 100)}... (${value.length} chars)` : value;
    } else if (typeof value === "number" || typeof value === "boolean") {
      result[key] = value;
    } else if (Array.isArray(value)) {
      result[key] = `Array(${value.length} items)`;
    } else if (typeof value === "object") {
      const keys = Object.keys(value as Record<string, unknown>);
      result[key] = `Object(${keys.length} keys: ${keys.slice(0, 5).join(", ")}${keys.length > 5 ? "..." : ""})`;
    } else {
      result[key] = String(value).slice(0, 50);
    }
  }

  const newTokenCount = estimateJsonTokens(result);
  const tokensSaved = Math.max(0, entry.tokenCount - newTokenCount);

  return { summary: result, tokensSaved };
}

// ── Single-entry 3-level escalation ──

/**
 * Attempt to summarize a single entry with 3-level escalation.
 *
 * Level 1 (normal): Standard compression prompt at targetRatio.
 * Level 2 (aggressive): More aggressive prompt at aggressiveRatio.
 * Level 3 (deterministic): Key-value extraction, no LLM needed.
 *
 * Each level only fires if the previous level failed or didn't reduce enough.
 */
async function summarizeWithEscalation(
  entry: ContextEntry,
  config: ResolvedConfig,
): Promise<SummarizationAttempt> {
  const originalTokens = entry.tokenCount;
  const serialized = JSON.stringify(entry.output, null, 2);

  // ── Level 1: Normal summarization ──
  try {
    const { content } = await callChatApi(config, [
      { role: "system", content: SYSTEM_PROMPT_NORMAL },
      { role: "user", content: buildSummarizationPrompt(entry.output, config.targetRatio, entry.nodeId, "normal") },
    ]);

    const parsed = parseLlmJson(content);
    if (parsed !== null) {
      const newTokens = estimateJsonTokens(parsed);
      const tokensSaved = originalTokens - newTokens;

      // Success if we saved at least 10% of original tokens
      if (tokensSaved > originalTokens * 0.1) {
        return { summary: parsed, level: "normal", tokensSaved };
      }

      // Level 1 didn't compress enough — escalate to Level 2
      console.log(
        `[context-compression] Level 1 summary for node "${entry.nodeId}" insufficient ` +
        `(${newTokens} tokens, saved ${tokensSaved}/${originalTokens}), escalating to Level 2`,
      );
    }
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[context-compression] Level 1 LLM summarization failed for node "${entry.nodeId}": ${msg}`);
  }

  // ── Level 2: Aggressive summarization ──
  try {
    const { content } = await callChatApi(config, [
      { role: "system", content: SYSTEM_PROMPT_AGGRESSIVE },
      { role: "user", content: buildSummarizationPrompt(entry.output, config.aggressiveRatio, entry.nodeId, "aggressive") },
    ]);

    const parsed = parseLlmJson(content);
    if (parsed !== null) {
      const newTokens = estimateJsonTokens(parsed);
      const tokensSaved = originalTokens - newTokens;

      // Even aggressive should save at least 5%
      if (tokensSaved > originalTokens * 0.05) {
        return { summary: parsed, level: "aggressive", tokensSaved };
      }
    }
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[context-compression] Level 2 LLM summarization failed for node "${entry.nodeId}": ${msg}`);
  }

  // ── Level 3: Deterministic fallback ──
  if (config.enableDeterministicFallback) {
    const { summary, tokensSaved } = deterministicFallback(entry);
    return { summary, level: "deterministic", tokensSaved };
  }

  return { summary: null, level: "deterministic", tokensSaved: 0, error: "All escalation levels failed" };
}

// ── Provenance ──

/**
 * Compute a short hash of a JSON-serializable value for provenance tracking.
 */
function summaryHash(value: unknown): string {
  const serialized = JSON.stringify(value);
  return createHash("sha256").update(serialized).digest("hex").slice(0, 12);
}

// ── Public API ──

/** Result of LLM summarization for a set of context entries. */
export type LlmSummarizeResult = {
  /** The compressed entries (with summarized outputs replacing originals). */
  entries: ContextEntry[];
  /** Provenance metadata for each compressed entry (keyed by nodeId). */
  provenance: Map<string, CompressionProvenance>;
  /** Token usage from LLM API calls (if available). */
  usage?: { inputTokens: number; outputTokens: number };
  /** Number of entries successfully summarized. */
  summarizedCount: number;
  /** Number of entries that failed summarization (fell back to verbatim). */
  failedCount: number;
  /** Number of entries that used Level 2 aggressive summarization. */
  aggressiveCount: number;
  /** Number of entries that used Level 3 deterministic fallback. */
  deterministicCount: number;
};

/**
 * Apply LLM-based summarization to eligible context entries with 3-level escalation.
 *
 * Only summarizes entries that:
 * - Have a content type in the eligible set (default: prose, mixed, data)
 * - Exceed the minimum token threshold (default: 200 tokens)
 * - Are not already compressed by a previous step
 * - Are not within the recency window (most recent N entries by priority order)
 *
 * Entries that fail all three levels fall back to verbatim output.
 * Entries with content type "code" or "error" are never summarized.
 *
 * Escalation strategy:
 * 1. Normal summarization at targetRatio (default 30%)
 * 2. Aggressive summarization at aggressiveRatio (default 15%)
 * 3. Deterministic key-value extraction (no LLM needed)
 *
 * @param entries - Context entries to potentially summarize
 * @param config - LLM summarizer configuration
 * @returns Summarize result with updated entries, provenance, and usage stats
 */
export async function summarizeContextEntries(
  entries: ContextEntry[],
  config?: LlmSummarizerConfig,
): Promise<LlmSummarizeResult> {
  const resolved = resolveConfig(config);

  // Identify entries protected by recency window
  // Sort by priority (highest first = lowest PRIORITY_ORDER number = most important)
  // then protect the most recent N entries
  const sortedByPriority = [...entries]
    .map((e, idx) => ({ entry: e, idx }))
    .sort((a, b) => {
      const pa = PRIORITY_ORDER[a.entry.priority];
      const pb = PRIORITY_ORDER[b.entry.priority];
      if (pa !== pb) return pa - pb; // higher priority first
      return b.entry.depth - a.entry.depth; // closer (lower depth) = more recent
    });

  const protectedIndices = new Set<number>();
  for (let i = 0; i < Math.min(resolved.recencyWindow, sortedByPriority.length); i++) {
    protectedIndices.add(sortedByPriority[i]!.idx);
  }

  // Separate eligible vs ineligible entries
  const eligible: Array<{ nodeId: string; output: Record<string, unknown>; index: number }> = [];
  const result: ContextEntry[] = [...entries]; // clone to preserve order
  const provenance = new Map<string, CompressionProvenance>();

  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i];
    // Skip already-compressed, code, error, small, and recency-protected entries
    if (
      entry.compressed
      || !resolved.eligibleContentTypes.has(entry.contentType)
      || entry.tokenCount < resolved.minTokensForSummarization
      || protectedIndices.has(i)
    ) {
      continue;
    }
    eligible.push({ nodeId: entry.nodeId, output: entry.output, index: i });
  }

  if (eligible.length === 0 || !resolved.baseUrl || !resolved.apiKey) {
    return {
      entries: result,
      provenance,
      summarizedCount: 0,
      failedCount: 0,
      aggressiveCount: 0,
      deterministicCount: 0,
    };
  }

  // Summarize each eligible entry with escalation (parallelized)
  const outcomes = await Promise.all(
    eligible.map(async ({ index }) => {
      const entry = entries[index]!;
      return summarizeWithEscalation(entry, resolved);
    }),
  );

  let summarizedCount = 0;
  let failedCount = 0;
  let aggressiveCount = 0;
  let deterministicCount = 0;
  let totalInputTokens = 0;
  let totalOutputTokens = 0;

  for (let si = 0; si < outcomes.length; si++) {
    const outcome = outcomes[si]!;
    const { index } = eligible[si]!;
    const original = entries[index]!;

    if (outcome.summary !== null) {
      const newTokenCount = estimateJsonTokens(outcome.summary);
      const version = 1; // First compression
      const hash = summaryHash(outcome.summary);

      result[index] = {
        ...original,
        output: {
          ...outcome.summary,
          _summarized: true,
          _summarizationLevel: outcome.level,
          _originalTokenCount: original.tokenCount,
          _summarizationModel: outcome.level !== "deterministic" ? resolved.model : "deterministic",
          _summaryHash: hash,
          _version: version,
        },
        tokenCount: newTokenCount,
        compressed: true,
      };

      provenance.set(original.nodeId, {
        method: "llm-summarize",
        originalTokenCount: original.tokenCount,
        originalOutput: original.output,
        version,
        summaryHash: hash,
      });

      if (outcome.level === "normal") {
        summarizedCount++;
      } else if (outcome.level === "aggressive") {
        aggressiveCount++;
      } else {
        deterministicCount++;
      }
    } else {
      failedCount++;
    }
  }

  return {
    entries: result,
    provenance,
    usage: (totalInputTokens > 0 || totalOutputTokens > 0)
      ? { inputTokens: totalInputTokens, outputTokens: totalOutputTokens }
      : undefined,
    summarizedCount,
    failedCount,
    aggressiveCount,
    deterministicCount,
  };
}

/**
 * Check if LLM summarization is available (has required config).
 */
export function isLlmSummarizationAvailable(config?: LlmSummarizerConfig): boolean {
  const resolved = resolveConfig(config);
  return resolved.baseUrl.length > 0 && resolved.apiKey.length > 0;
}