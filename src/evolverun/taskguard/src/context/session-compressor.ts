/**
 * Session-level context compression orchestration.
 *
 * Combines tool-output-prepass (deterministic, zero-cost) with sliding-window
 * compaction to keep session JSONL files within token budgets. Provides both
 * in-memory and file-based compression APIs.
 *
 * @module context/session-compressor
 */

import { dirname } from "node:path";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";

import type { SessionMessage, ParsedSession, RawSessionLine } from "./session-reader.js";
import {
  readSessionFile,
  readSessionFileRaw,
  writeSessionFile,
  writeSessionFileRaw,
  createSystemMessage,
  estimateSessionTokens,
  modifyToolResultContent,
} from "./session-reader.js";
import { applyToolOutputPrepass } from "./tool-output-prepass.js";
import {
  applyTieredCompaction,
  CircuitBreaker,
  type CompactionLevel,
  type CompactionThresholds,
  type TieredCompactionOptions,
  type CircuitBreakerState,
} from "./tiered-compaction.js";

// ── Configuration ──

/** Configuration for session compression. */
export type SessionCompressionConfig = {
  /** Tool output prepass enabled. Default: true */
  toolPrepassEnabled: boolean;
  /** Maximum chars for a single tool result before truncation. Default: 5000 */
  toolResultMaxChars: number;
  /** Number of recent message pairs to keep unchanged. Default: 6 */
  recencyWindow: number;
  /** Token budget for the compressed session. Default: 50000 */
  maxSessionTokens: number;
  /** Whether to insert a compaction notice system message. Default: true */
  insertCompactionNotice: boolean;
  /** Whether to deduplicate repeated file reads. Default: true */
  deduplicateReads: boolean;
  /** TTL in ms for read dedup cache. Default: 300000 (5min) */
  readDedupTtlMs: number;
  /** Minimum session size (tokens) to trigger compression. Default: 30000 */
  minTokensToCompact: number;
  /**
   * Target token budget for the session within the context window.
   * Used to compute effectiveMaxSessionTokens (budget minus non-session overhead)
   * and as the denominator for budgetRatio calculations.
   * Falls back to maxSessionTokens if unset.
   */
  contextTokenBudget?: number;
  /**
   * The model's actual context window size (tokens).
   * Used for the overhead-dominated check: if non-session overhead (system
   * prompt + tool definitions + skills) alone exceeds this ceiling, session
   * compression is mathematically futile and is skipped early.
   * Should match the model's real limit (e.g., 131072 for Kimi-K2.5,
   * 200000 for Claude Sonnet). Falls back to contextTokenBudget if unset.
   */
  modelContextWindow?: number;
  /**
   * Skip the file-size heuristic check in maybeCompactSessionFile.
   * When true, the function always parses the session file and runs compression
   * regardless of the file's byte size. This is needed when the caller already
   * knows the actual prompt token count (e.g., from llm_output hook) and the
   * bulk of those tokens come from system prompt / tool definitions / skill
   * content that are NOT reflected in the session file size.
   * Default: false
   */
  skipSizeHeuristic?: boolean;
  /**
   * Actual total prompt token count as reported by the LLM API
   * (input + cacheRead). When provided alongside contextTokenBudget,
   * this allows maybeCompactSessionFile to calculate a tighter effective
   * session budget that accounts for non-session overhead (system prompt,
   * tool definitions, skill content).
   *
   * Without this, the compressor only checks session file tokens against
   * maxSessionTokens, which can incorrectly skip compression when the
   * session file is small but the total prompt is over budget due to
   * non-session overhead.
   *
   * Set by the llm_output hook when actual API token counts are available.
   */
  actualPromptTokens?: number;
  /**
   * Number of messages at the start of the session to preserve unchanged
   * as the Anthropic prefix cache anchor. These messages form the stable
   * prefix that enables cache hits across turns — if any of these messages
   * are modified or removed, the cache prefix breaks and all subsequent
   * turns are billed as fresh input tokens.
   *
   * The prepass (tool output truncation) and sliding window (eviction) only
   * operate on messages AFTER this prefix. The compaction notice is also
   * inserted after the prefix.
   *
   * Default: 3 (system message + first user message + first assistant response)
   * Set to 0 to disable prefix preservation (original behavior).
   */
  cachePrefixMessages?: number;

  // ── Tiered Compaction Options ──

  /**
   * Whether to enable tiered compaction (Claw Compactor-inspired multi-level
   * compression). When enabled, compactSession selects a compaction level
   * (micro/auto/full) based on context pressure and applies progressively
   * more aggressive strategies.
   *
   * When disabled (default for backward compat), the original pipeline is used:
   * tool-output-prepass → sliding window → compaction notice.
   *
   * When enabled, the tiered pipeline replaces the original:
   * - micro (60%): budgetToolResults (age-based truncation)
   * - auto (80%): micro + semantic dedup + conversation summarization
   * - full (95%): auto + tool-output-prepass + sliding window
   *
   * Default: true
   */
  tieredCompactionEnabled?: boolean;

  /**
   * Thresholds for each compaction level as fractions of the effective budget.
   * Overrides the defaults (micro: 0.6, auto: 0.8, full: 0.95).
   */
  compactionThresholds?: Partial<CompactionThresholds>;

  /**
   * Number of recent tool results to keep untruncated in the budget stage.
   * Only used when tiered compaction is enabled.
   * Default: 5
   */
  budgetKeepRecent?: number;

  /**
   * Maximum tokens per tool result before considering it oversized.
   * Only used when tiered compaction is enabled.
   * Default: 8000
   */
  budgetMaxTokensPerResult?: number;

  /**
   * Number of recent user+assistant turns to preserve verbatim during
   * conversation summarization (auto/full levels).
   * Default: 4
   */
  summaryPreserveRecentTurns?: number;

  /**
   * Maximum tokens for the generated conversation summary.
   * Default: 5000
   */
  summaryMaxTokens?: number;

  /**
   * SimHash Hamming distance threshold for near-duplicate detection.
   * Messages with distance ≤ this value are considered duplicates.
   * Default: 3
   */
  dedupThreshold?: number;
};

/** Default values for SessionCompressionConfig. */
export const SESSION_COMPRESSION_DEFAULTS: SessionCompressionConfig = {
  toolPrepassEnabled: true,
  toolResultMaxChars: 5000,
  recencyWindow: 6,
  maxSessionTokens: 50000,
  insertCompactionNotice: true,
  deduplicateReads: true,
  readDedupTtlMs: 300_000,
  minTokensToCompact: 30000,
  cachePrefixMessages: 3,
  // Tiered compaction defaults
  tieredCompactionEnabled: true,
  budgetKeepRecent: 5,
  budgetMaxTokensPerResult: 8000,
  summaryPreserveRecentTurns: 4,
  summaryMaxTokens: 5000,
  dedupThreshold: 3,
};

// ── Circuit Breaker ──

/**
 * Module-level circuit breaker for tiered compaction.
 *
 * Persists across compactSession calls — if compaction fails
 * MAX_CONSECUTIVE_FAILURES (3) times in a row, further attempts
 * are skipped until the next successful compaction or manual reset.
 */
const compactionCircuitBreaker = new CircuitBreaker();

/**
 * Reset the global compaction circuit breaker.
 *
 * Call this at the start of a new session or when the compression
 * environment has changed meaningfully (e.g., after a config update).
 */
export function resetCompactionCircuitBreaker(): void {
  compactionCircuitBreaker.reset();
}

// ── Result ──

/** Statistics from a session compression run. */
export type SessionCompressionStats = {
  /** Number of input messages. */
  inputMessages: number;
  /** Number of output messages. */
  outputMessages: number;
  /** Estimated token count before compression. */
  inputTokens: number;
  /** Estimated token count after compression. */
  outputTokens: number;
  /** Compression ratio (outputTokens / inputTokens), 1.0 = no change. */
  compressionRatio: number;
  /** Number of tool result messages that were compressed. */
  toolResultsCompressed: number;
  /** Number of messages summarized or trimmed. */
  messagesSummarized: number;
  /** Number of messages evicted entirely. */
  messagesEvicted: number;
  /** Names of tool-output-prepass rules that were applied. */
  rulesApplied: string[];
  /** Wall-clock time in ms. */
  durationMs: number;
  /** Whether any compression actually happened. */
  wasCompressed: boolean;
  /** When wasCompressed is false, explains why compression was skipped. */
  skipReason?: CompactSkipReason;
  /** Tiered compaction level applied (none/micro/auto/full). Only set when tieredCompactionEnabled is true. */
  compactionLevel?: CompactionLevel;
  /** Circuit breaker state after compaction (tiered mode only). */
  circuitBreaker?: CircuitBreakerState;
};

/** Result of compressing a session. */
export type SessionCompressionResult = {
  /** Compressed messages (new array, no mutation of input). */
  messages: SessionMessage[];
  /** Compression statistics. */
  stats: SessionCompressionStats;
};

// ── Core: compactSession ──

/**
 * Compress an in-memory list of session messages.
 *
 * When `tieredCompactionEnabled` is true (default), uses a Claw Compactor-inspired
 * tiered pipeline that selects the compression level based on context pressure:
 *
 * - **none** (<60% budget): No compression — session is under budget.
 * - **micro** (60-80%): Age-based tool result budget truncation only.
 * - **auto** (80-95%): micro + cross-message semantic dedup + conversation summarization.
 * - **full** (≥95%): auto + content-aware prepass + sliding window eviction.
 *
 * When `tieredCompactionEnabled` is false, uses the original pipeline:
 * tool-output-prepass → sliding window → compaction notice.
 *
 * In both cases, the cache prefix (first N messages) is preserved unchanged,
 * and the compaction notice is inserted after the prefix.
 *
 * Returns a new messages array with compression statistics.
 * No mutation of the input array.
 */
export function compactSession(
  messages: readonly SessionMessage[],
  config?: Partial<SessionCompressionConfig>,
): SessionCompressionResult {
  const startMs = Date.now();
  const cfg = { ...SESSION_COMPRESSION_DEFAULTS, ...config };

  const inputTokens = estimateSessionTokens(messages);
  const inputMessages = messages.length;

  // Skip compression if under the minimum threshold
  if (inputTokens < cfg.minTokensToCompact) {
    return {
      messages: [...messages],
      stats: buildStats(inputMessages, inputMessages, inputTokens, inputTokens, 0, 0, 0, [], startMs, false, "under-budget", "none"),
    };
  }

  // Step 0: Separate cache prefix from compressible body
  const prefixCount = cfg.cachePrefixMessages ?? 3;
  const prefix: SessionMessage[] = messages.slice(0, Math.min(prefixCount, messages.length));
  const body: SessionMessage[] = messages.slice(prefixCount);
  const prefixTokens = estimateSessionTokens(prefix);
  const bodyTokenBudget = Math.max(0, cfg.maxSessionTokens - prefixTokens);

  console.log(
    `[session-compressor] compactSession: cachePrefixMessages=${prefixCount}, ` +
    `prefix=${prefix.length} msgs/${prefixTokens} tokens, ` +
    `body=${body.length} msgs, bodyBudget=${bodyTokenBudget} tokens, ` +
    `tiered=${cfg.tieredCompactionEnabled}`,
  );

  // If the body is empty (session has fewer messages than cachePrefixMessages),
  // nothing to compress — return as-is
  if (body.length === 0) {
    return {
      messages: [...messages],
      stats: buildStats(inputMessages, inputMessages, inputTokens, inputTokens, 0, 0, 0, [], startMs, false, "body-empty"),
    };
  }

  // ── Tiered Compaction Pipeline ──
  if (cfg.tieredCompactionEnabled) {
    return compactSessionTiered(messages, prefix, body, prefixTokens, bodyTokenBudget, cfg, startMs);
  }

  // ── Legacy Pipeline (prepass → sliding window) ──
  return compactSessionLegacy(messages, prefix, body, prefixTokens, bodyTokenBudget, cfg, startMs);
}

// ── Tiered Compaction Pipeline ──

/**
 * Tiered compaction pipeline (Claw Compactor-inspired).
 *
 * Delegates to applyTieredCompaction for the core micro/auto/full stages,
 * then applies session-specific full-stage extensions (content-aware prepass
 * and sliding window eviction) that are not part of the standalone tiered
 * compaction module.
 *
 * Uses a module-level CircuitBreaker that persists across calls — if
 * compaction fails 3 consecutive times, further attempts are skipped until
 * the next success or manual reset via resetCompactionCircuitBreaker().
 */
function compactSessionTiered(
  allMessages: readonly SessionMessage[],
  prefix: SessionMessage[],
  body: SessionMessage[],
  prefixTokens: number,
  bodyTokenBudget: number,
  cfg: SessionCompressionConfig,
  startMs: number,
): SessionCompressionResult {
  const inputTokens = estimateSessionTokens(allMessages);
  const inputMessages = allMessages.length;

  // ── Delegate to applyTieredCompaction for micro/auto stages ──
  const tieredResult = applyTieredCompaction(body, {
    effectiveBudget: bodyTokenBudget,
    thresholds: cfg.compactionThresholds,
    budgetOptions: {
      keepRecent: cfg.budgetKeepRecent,
      maxTokensPerResult: cfg.budgetMaxTokensPerResult,
    },
    summarizeOptions: {
      preserveRecentTurns: cfg.summaryPreserveRecentTurns,
      maxSummaryTokens: cfg.summaryMaxTokens,
    },
    dedupThreshold: cfg.dedupThreshold,
    circuitBreaker: compactionCircuitBreaker,
  });

  let current = tieredResult.messages;
  let toolResultsCompressed = 0;
  let messagesSummarized = 0;
  let messagesEvicted = 0;
  const rulesApplied: string[] = [];

  // ── Collect stats from tiered compaction stages ──
  if (tieredResult.stages.budget) {
    const b = tieredResult.stages.budget;
    toolResultsCompressed += b.truncatedCount + b.oversizedCount;
    if (b.truncatedCount > 0 || b.oversizedCount > 0) {
      rulesApplied.push(`budget-age:${b.truncatedCount}truncated/${b.oversizedCount}oversized`);
    }
  }

  if (tieredResult.stages.dedup) {
    const d = tieredResult.stages.dedup;
    messagesSummarized += d.dedupedCount;
    if (d.dedupedCount > 0) {
      rulesApplied.push(`semantic-dedup:${d.dedupedCount}`);
    }
  }

  if (tieredResult.stages.summary) {
    const s = tieredResult.stages.summary;
    messagesSummarized += s.turnsSummarized;
    if (s.triggered && s.turnsSummarized > 0) {
      rulesApplied.push(`summary:${s.turnsSummarized}turns`);
    }
  }

  console.log(
    `[session-compressor] tiered: level=${tieredResult.level}, ` +
    `bodyTokens=${tieredResult.inputTokens}, bodyBudget=${bodyTokenBudget}, ` +
    `afterTiered=${tieredResult.outputTokens}, ` +
    `circuitBreaker=${tieredResult.circuitBreaker.disabled ? "TRIPPED" : "ok"}`,
  );

  // ── Full stage: content-aware prepass + sliding window ──
  // These are session-specific compression steps that go beyond what
  // applyTieredCompaction provides (which leaves them as stubs).
  if (tieredResult.level === "full") {
    // Content-aware tool output prepass
    if (cfg.toolPrepassEnabled) {
      const prepassResult = applyToolOutputPrepass(current, {
        maxResultChars: cfg.toolResultMaxChars,
        readDedupTtlMs: cfg.readDedupTtlMs,
      });
      current = prepassResult.messages;
      toolResultsCompressed += prepassResult.compressedCount;
      rulesApplied.push(...prepassResult.rulesApplied);
    }

    // Dynamic recencyWindow for short sessions
    let effectiveRecencyWindow = cfg.recencyWindow;
    const bodyNonSystemCount = current.filter(m => m.role !== "system").length;
    const protectedThreshold = (cfg.recencyWindow * 2) + 2;
    if (bodyNonSystemCount <= protectedThreshold) {
      effectiveRecencyWindow = 1;
    }

    // Sliding window eviction
    const windowResult = slidingWindowCompact(current, effectiveRecencyWindow, bodyTokenBudget);
    current = windowResult.messages;
    messagesEvicted = windowResult.evictedCount;

    console.log(
      `[session-compressor] tiered full: prepass=${toolResultsCompressed}, ` +
      `windowEvicted=${messagesEvicted}`,
    );
  }

  // ── Compaction notice ──
  if (cfg.insertCompactionNotice && (messagesEvicted > 0 || messagesSummarized > 0)) {
    const notice = compactionNoticeMessage(
      inputMessages,
      prefix.length + current.length + 1,
      inputTokens - (prefixTokens + estimateSessionTokens(current)),
    );
    current = [notice, ...current];
  }

  // Reassemble: prefix + compressed body
  const result = [...prefix, ...current];
  const outputTokens = estimateSessionTokens(result);
  const outputMessages = result.length;
  const wasCompressed = outputTokens < inputTokens;

  // Determine skip reason
  let skipReason: CompactSkipReason | undefined;
  if (!wasCompressed) {
    if (tieredResult.level === "none") {
      skipReason = "under-budget";
    } else if (tieredResult.circuitBreaker.disabled) {
      skipReason = "under-budget"; // Circuit breaker tripped — compression disabled
    } else {
      skipReason = "under-budget"; // Tiered stages ran but didn't reduce enough
    }
  }

  return {
    messages: result,
    stats: buildStats(
      inputMessages, outputMessages, inputTokens, outputTokens,
      toolResultsCompressed, messagesSummarized, messagesEvicted,
      rulesApplied, startMs,
      wasCompressed,
      skipReason,
      tieredResult.level,
      tieredResult.circuitBreaker,
    ),
  };
}

// ── Legacy Pipeline (prepass → sliding window) ──

/**
 * Legacy compression pipeline for backward compatibility.
 *
 * Pipeline: tool-output-prepass → sliding window → compaction notice.
 * Used when `tieredCompactionEnabled` is false.
 */
function compactSessionLegacy(
  allMessages: readonly SessionMessage[],
  prefix: SessionMessage[],
  body: SessionMessage[],
  prefixTokens: number,
  bodyTokenBudget: number,
  cfg: SessionCompressionConfig,
  startMs: number,
): SessionCompressionResult {
  const inputTokens = estimateSessionTokens(allMessages);
  const inputMessages = allMessages.length;

  // Step 1: Tool output prepass (only on body, not prefix)
  let current: SessionMessage[] = [...body];
  let toolResultsCompressed = 0;
  let rulesApplied: string[] = [];
  let messagesSummarized = 0;

  if (cfg.toolPrepassEnabled) {
    const prepassResult = applyToolOutputPrepass(current, {
      maxResultChars: cfg.toolResultMaxChars,
      readDedupTtlMs: cfg.readDedupTtlMs,
    });
    current = prepassResult.messages;
    toolResultsCompressed = prepassResult.compressedCount;
    rulesApplied = prepassResult.rulesApplied;
    messagesSummarized = prepassResult.compressedCount;
  }

  // Step 2: Sliding window compaction (only on body, with reduced budget)
  // Dynamic recencyWindow: if the body has too few non-system messages,
  // the default recencyWindow protects all of them, making eviction impossible.
  // Reduce recencyWindow to 1 pair when body messages are within the protected range.
  let effectiveRecencyWindow = cfg.recencyWindow;
  const bodyNonSystemCount = current.filter(m => m.role !== "system").length;
  const protectedThreshold = (cfg.recencyWindow * 2) + 2;
  if (bodyNonSystemCount <= protectedThreshold) {
    effectiveRecencyWindow = 1;
    console.log(
      `[session-compressor] compactSession: reducing recencyWindow ${cfg.recencyWindow}→1 ` +
      `(bodyNonSystem=${bodyNonSystemCount} ≤ protectedThreshold=${protectedThreshold})`,
    );
  }

  const windowResult = slidingWindowCompact(current, effectiveRecencyWindow, bodyTokenBudget);
  current = windowResult.messages;
  const messagesEvicted = windowResult.evictedCount;

  // Step 3: Compaction notice (inserted after prefix, not at the beginning)
  if (cfg.insertCompactionNotice && messagesEvicted > 0) {
    const notice = compactionNoticeMessage(
      inputMessages,
      prefix.length + current.length + 1, // +1 for the notice itself
      inputTokens - (prefixTokens + estimateSessionTokens(current)),
    );
    current = [notice, ...current];
  }

  // Reassemble: prefix + compressed body
  const result = [...prefix, ...current];

  const outputTokens = estimateSessionTokens(result);
  const outputMessages = result.length;
  const wasCompressed = outputTokens < inputTokens;

  // Determine skip reason when no compression happened
  let skipReason: CompactSkipReason | undefined;
  if (!wasCompressed) {
    if (windowResult.skipReason === "already-under-budget") {
      skipReason = "under-budget";
    } else if (windowResult.skipReason === "all-protected") {
      skipReason = "recency-protected";
    } else if (messagesEvicted === 0 && toolResultsCompressed === 0) {
      skipReason = "recency-protected";
    } else {
      skipReason = "under-budget";
    }
  }

  return {
    messages: result,
    stats: buildStats(
      inputMessages, outputMessages, inputTokens, outputTokens,
      toolResultsCompressed, messagesSummarized, messagesEvicted,
      rulesApplied, startMs,
      wasCompressed,
      skipReason,
    ),
  };
}

/**
 * Read a session file, compress it, and write the result to a new file.
 *
 * If the file doesn't exist or is below the minimum threshold,
 * returns the original path without modification.
 */
export async function compactSessionFile(
  inputPath: string,
  outputPath: string,
  config?: Partial<SessionCompressionConfig>,
): Promise<SessionCompressionResult> {
  const parsed = await readSessionFile(inputPath);
  if (parsed.messages.length === 0) {
    // Empty or missing file — write empty output and return
    await writeSessionFile(outputPath, []);
    return {
      messages: [],
      stats: buildStats(0, 0, 0, 0, 0, 0, 0, [], Date.now(), false),
    };
  }

  const result = compactSession(parsed.messages, config);

  const dir = dirname(outputPath);
  await mkdir(dir, { recursive: true });
  await writeSessionFile(outputPath, result.messages);

  return result;
}

/** Why compactSession decided not to compress, exposed for logging/observability. */
export type CompactSkipReason =
  /** Session tokens are below minTokensToCompact — nothing to worry about. */
  | "under-budget"
  /** After separating the cache prefix, the compressible body is empty. */
  | "body-empty"
  /** The recency window protects all body messages; nothing can be evicted. */
  | "recency-protected";

export type MaybeCompactResult =
  | { kind: "compressed"; stats: SessionCompressionStats }
  | { kind: "skipped"; reason: "file-not-found" | "file-too-small" | "no-messages" | "under-budget" | "overhead-dominated" | "body-empty" | "recency-protected"; inputTokens?: number; inputMessages?: number }
  | { kind: "error"; error: string };

/**
 * Conditionally compress a session file in-place if it exceeds the threshold.
 *
 * Always returns a `MaybeCompactResult` with a `kind` discriminator so callers
 * can distinguish between "compression succeeded", "skipped (with reason)",
 * and "error" — enabling actionable logging.
 *
 * Writes the compressed version back to the same file only when compression
 * actually reduced the token count.
 */
export async function maybeCompactSessionFile(
  filePath: string,
  config?: Partial<SessionCompressionConfig>,
): Promise<MaybeCompactResult> {
  // Check file size first as a cheap heuristic
  let fileSize: number;
  try {
    const info = await stat(filePath);
    fileSize = info.size;
  } catch {
    return { kind: "skipped", reason: "file-not-found" };
  }

  const cfg = { ...SESSION_COMPRESSION_DEFAULTS, ...config };

  // If file is small, skip compression — UNLESS the caller already knows the
  // actual prompt token count is over budget (skipSizeHeuristic=true).
  // When triggered by llm_output, the actual API token count includes system
  // prompt, tool definitions, skill content, etc. that aren't in the session
  // file, so the file-size heuristic would undercount and incorrectly skip.
  if (!cfg.skipSizeHeuristic) {
    // JSONL files have ~88% JSON metadata overhead; measured ratio is ~31 bytes per actual message token.
    // Using /12 provides a ~2.5x conservative overestimate vs the old /4 which was ~8x over.
    const FILE_SIZE_TOKEN_DIVISOR = 12;
    const roughTokenEstimate = fileSize / FILE_SIZE_TOKEN_DIVISOR;
    if (roughTokenEstimate < cfg.minTokensToCompact) {
      return { kind: "skipped", reason: "file-too-small", inputTokens: Math.round(roughTokenEstimate) };
    }
  }

  let parsed: ParsedSession;
  try {
    parsed = await readSessionFile(filePath);
  } catch (err) {
    return { kind: "error", error: err instanceof Error ? err.message : String(err) };
  }

  if (parsed.messages.length === 0) {
    return { kind: "skipped", reason: "no-messages", inputTokens: parsed.totalTokens };
  }

  // When actualPromptTokens is provided (e.g., from llm_output hook), calculate
  // a tighter effective maxSessionTokens that accounts for non-session overhead.
  //
  // The model's context window is the hard ceiling:
  //   overhead + sessionTokens <= modelContextWindow
  //   sessionTokens <= modelContextWindow - overhead
  //
  // contextTokenBudget is a soft target (how full we *want* the context to be),
  // NOT the ceiling. Using contextTokenBudget as the ceiling causes:
  //   effectiveMaxSessionTokens = contextTokenBudget(10K) - overhead(75K) = 0
  // even when the model window has 128K of room. The compressor then runs
  // with bodyBudget=0, achieves nothing, and logs "Adjusting → 0" 67% of the time.
  //
  // Instead, we use modelContextWindow as the ceiling for the effective budget
  // calculation, and contextTokenBudget as the utilization target: when the
  // model window is partially filled, we aim to keep the session within
  // contextTokenBudget% of the remaining space.
  let effectiveCfg = cfg;
  if (cfg.actualPromptTokens && cfg.actualPromptTokens > 0) {
    const nonSessionOverhead = cfg.actualPromptTokens - parsed.totalTokens;
    if (nonSessionOverhead > 0) {
      // The model's actual context window — the hard ceiling.
      // Falls back to contextTokenBudget only if modelContextWindow is not set
      // (in which case contextTokenBudget IS the ceiling by necessity).
      const contextCeiling = cfg.modelContextWindow ?? cfg.contextTokenBudget ?? cfg.maxSessionTokens;

      // Overhead-dominated: non-session content alone exceeds the model's context
      // window. Compressing the session file to zero tokens would not bring the
      // prompt within the window — the bloat is in system prompt / tool definitions
      // / skill content, which this compressor cannot touch. Skip early with a
      // clear reason.
      if (nonSessionOverhead >= contextCeiling) {
        console.log(
          `[session-compressor] Overhead-dominated: nonSessionOverhead=${nonSessionOverhead} >= ` +
          `contextCeiling=${contextCeiling}` +
          (cfg.modelContextWindow ? " (modelContextWindow)" : " (contextTokenBudget fallback)") +
          `, sessionTokens=${parsed.totalTokens}. Session compression cannot fix this; ` +
          `the bloat is in system prompt / tool definitions / skill content.`,
        );
        return {
          kind: "skipped",
          reason: "overhead-dominated",
          inputTokens: parsed.totalTokens,
          inputMessages: parsed.messages.length,
        };
      }

      // Calculate effective session budget using the model's context window as ceiling.
      // This gives session compression meaningful room to work with:
      //   modelContextWindow(128K) - overhead(75K) = 53K effective session budget
      // vs the old broken formula:
      //   contextTokenBudget(10K) - overhead(75K) = -65K → 0 (useless)
      const effectiveMaxSessionTokens = Math.max(
        Math.floor(contextCeiling - nonSessionOverhead),
        0,
      );
      if (effectiveMaxSessionTokens < cfg.maxSessionTokens) {
        console.log(
          `[session-compressor] Adjusting maxSessionTokens: ${cfg.maxSessionTokens} → ${effectiveMaxSessionTokens} ` +
          `(actualPrompt=${cfg.actualPromptTokens}, sessionTokens=${parsed.totalTokens}, ` +
          `overhead=${nonSessionOverhead}, ceiling=${contextCeiling}` +
          (cfg.modelContextWindow ? " (modelContextWindow)" : " (contextTokenBudget fallback)") +
          `)`,
        );
        // When the caller provides actual API token counts (skipSizeHeuristic=true),
        // they already know the total prompt is over budget. Override minTokensToCompact
        // to 0 so compactSession doesn't skip based on session-file-only token counts.
        // Most of the prompt may be non-session overhead (system prompt, tool defs, skills),
        // making the session file look small even though the total prompt is massive.
        effectiveCfg = {
          ...cfg,
          maxSessionTokens: effectiveMaxSessionTokens,
          minTokensToCompact: 0,
        };
      }
    }
  }

  const result = compactSession(parsed.messages, effectiveCfg);

  // No compression happened — propagate the specific skip reason from compactSession.
  // Reasons: "under-budget" (session tokens below threshold),
  //          "body-empty" (nothing compressible after prefix separation),
  //          "recency-protected" (recency window guards all messages).
  if (!result.stats.wasCompressed) {
    return {
      kind: "skipped",
      reason: result.stats.skipReason ?? "under-budget",
      inputTokens: result.stats.inputTokens,
      inputMessages: result.stats.inputMessages,
    };
  }

  await writeSessionFile(filePath, result.messages);
  return { kind: "compressed", stats: result.stats };
}

// ── Safe (sidecar) compaction ──

/** Suffix appended to a session file path to form its sidecar cache path. */
export const SIDECAR_SUFFIX = ".compressed.jsonl";

/** Derive the sidecar cache path for a given session file. */
export function sidecarPathFor(filePath: string): string {
  return `${filePath}${SIDECAR_SUFFIX}`;
}

/**
 * Result of {@link maybeCompactSessionFileSafe}. Mirrors {@link MaybeCompactResult}
 * but, on success, reports the sidecar path instead of leaving the original file
 * rewritten.
 */
export type MaybeCompactSafeResult =
  | { kind: "compressed"; sidecarPath: string; stats: SessionCompressionStats }
  | {
    kind: "skipped";
    reason:
      | "file-not-found"
      | "file-too-small"
      | "no-messages"
      | "under-budget"
      | "body-empty"
      | "recency-protected";
    inputTokens?: number;
    inputMessages?: number;
  }
  | { kind: "error"; error: string };

/**
 * Compress a session file WITHOUT rewriting it in place.
 *
 * This is the safe counterpart to {@link maybeCompactSessionFile}. It reads the
 * session file with FULL line preservation (structural entries like the
 * `type:"session"` header, `model_change`, `thinking_level_change`, and
 * `type:"compaction` durable summaries are kept verbatim), runs the same
 * {@link compactSession} pipeline on the message subset, then writes the
 * compressed output to a **sidecar** file (`<file>.compressed.jsonl`) via an
 * atomic temp-write + rename. The original session JSONL is never modified.
 *
 * The sidecar interleaving uses OBJECT IDENTITY to distinguish:
 *   - unchanged survivors (prefix + messages the prepass/window kept by
 *     reference) → placed at their original position, with all preceding
 *     structural entries flushed before them;
 *   - new/modified messages (truncated tool results, compaction notices — both
 *     are fresh objects) → emitted inline at their compressed position.
 *
 * Known limitation (acceptable for the P0 fix, documented for transparency):
 * structural entries interleaved BETWEEN a modified tool-result message and the
 * next unchanged survivor are deferred to just before that next survivor, i.e.
 * they may appear slightly earlier than their original position. No structural
 * entry is ever dropped — the previous in-place rewrite lost ALL of them.
 *
 * NOTE: unlike {@link maybeCompactSessionFile}, this variant does not implement
 * the `actualPromptTokens` overhead-dominated branch (the
 * `before_prompt_build` callers do not pass it). It uses the file-size
 * heuristic plus `compactSession`'s own threshold.
 */
export async function maybeCompactSessionFileSafe(
  filePath: string,
  config?: Partial<SessionCompressionConfig>,
): Promise<MaybeCompactSafeResult> {
  // 1. Cheap file-size heuristic (same divisor/ratio as maybeCompactSessionFile).
  let fileSize: number;
  try {
    const info = await stat(filePath);
    fileSize = info.size;
  } catch {
    return { kind: "skipped", reason: "file-not-found" };
  }

  const cfg = { ...SESSION_COMPRESSION_DEFAULTS, ...config };

  if (!cfg.skipSizeHeuristic) {
    const FILE_SIZE_TOKEN_DIVISOR = 12;
    const roughTokenEstimate = fileSize / FILE_SIZE_TOKEN_DIVISOR;
    if (roughTokenEstimate < cfg.minTokensToCompact) {
      return { kind: "skipped", reason: "file-too-small", inputTokens: Math.round(roughTokenEstimate) };
    }
  }

  // 2. Read with full structural-entry preservation.
  const raw = await readSessionFileRaw(filePath);
  if (raw.messages.length === 0) {
    return { kind: "skipped", reason: "no-messages", inputTokens: raw.totalTokens };
  }

  // 3. Run the same compaction pipeline used by the in-place variant.
  const result = compactSession(raw.messages, cfg);
  if (!result.stats.wasCompressed) {
    return {
      kind: "skipped",
      reason: result.stats.skipReason ?? "under-budget",
      inputTokens: result.stats.inputTokens,
      inputMessages: result.stats.inputMessages,
    };
  }

  // 4. Build the sidecar: interleave structural entries with compressed messages.
  const sidecarLines = buildSidecarLines(raw.rawLines, result.messages);

  // 5. Atomic write to the sidecar (temp file + rename).
  const sidecarPath = sidecarPathFor(filePath);
  try {
    await writeSessionFileRaw(sidecarPath, sidecarLines);
  } catch (err) {
    return { kind: "error", error: err instanceof Error ? err.message : String(err) };
  }

  return { kind: "compressed", sidecarPath, stats: result.stats };
}

/**
 * Interleave structural (non-message) entries with the compressed message list
 * to form the sidecar content.
 *
 * Positional mapping is by object identity: messages that survived compression
 * unchanged keep their original reference, so we can locate them in `rawLines`
 * and flush the structural entries that preceded them. Messages created or
 * rewritten by compression (modified tool results, compaction notices) are new
 * objects with no original position — they are emitted inline in compressed
 * order. See {@link maybeCompactSessionFileSafe} for the documented limitation.
 *
 * IMPORTANT: `compressedMessages` are NOT guaranteed to be in monotonically
 * increasing original-index order. `slidingWindowCompact` (and the tiered full
 * stage) reassemble the body as `[...systemMessages, ...nonSystemMessages]`,
 * pulling all body system messages to the front — so a surviving non-system
 * message with a LOWER original index than a preceding system survivor is
 * common. The scan cursor therefore must NEVER move backward (else intervening
 * structural entries get re-scanned and duplicated). See the regression test
 * `buildSidecarLines survives out-of-order survivors (no duplicate non_message)`.
 */
export function buildSidecarLines(
  rawLines: readonly RawSessionLine[],
  compressedMessages: readonly SessionMessage[],
): RawSessionLine[] {
  // Map each original message (by identity) to its rawLine index, so unchanged
  // survivors can be located positionally.
  const originalIndexByMessage = new Map<SessionMessage, number>();
  for (let i = 0; i < rawLines.length; i++) {
    const entry = rawLines[i];
    if (entry.kind === "message") {
      originalIndexByMessage.set(entry.message, i);
    }
  }

  const output: RawSessionLine[] = [];
  // High-water mark: the next rawLines index that still needs scanning for
  // not-yet-flushed structural entries. Monotonically non-decreasing — a
  // survivor whose original index is below the high-water mark (possible when
  // slidingWindowCompact reorders system messages forward) has already had its
  // preceding structural entries flushed, so we push it without re-scanning.
  let scanCursor = 0;

  const flushStructuralEntriesUpTo = (exclusiveEnd: number): void => {
    for (let i = scanCursor; i < exclusiveEnd; i++) {
      const entry = rawLines[i];
      // Skip message entries — they are either originals already handled below,
      // or originals that were evicted/modified (no longer relevant).
      if (entry.kind === "non_message") {
        output.push(entry);
      }
    }
    if (exclusiveEnd > scanCursor) {
      scanCursor = exclusiveEnd;
    }
  };

  for (const outMsg of compressedMessages) {
    const originalIdx = originalIndexByMessage.get(outMsg);
    if (originalIdx !== undefined) {
      // Unchanged survivor — flush structural entries preceding its original
      // position (only those not already flushed), then emit the survivor.
      flushStructuralEntriesUpTo(originalIdx);
      // Advance past the survivor's position, but NEVER move backward — the
      // reordering described above can make originalIdx < scanCursor.
      scanCursor = Math.max(scanCursor, originalIdx + 1);
      output.push({ kind: "message", raw: outMsg.raw, message: outMsg });
    } else {
      // New/modified message (truncated tool result or compaction notice) —
      // emit inline. Pending structural entries between the last survivor and
      // the next one are deferred (see function doc).
      output.push({ kind: "message", raw: outMsg.raw, message: outMsg });
    }
  }

  // 6. Flush any trailing structural entries after the last survivor.
  flushStructuralEntriesUpTo(rawLines.length);

  return output;
}

// ── Sliding Window ──

/** Why slidingWindowCompact returned messages unchanged. */
export type SlidingWindowSkipReason =
  /** Message tokens already fit within the budget — no eviction needed. */
  | "already-under-budget"
  /** Too few messages to apply the sliding window (<= recencyWindow + 1). */
  | "too-few-messages"
  /** All non-system messages fall within the recency window — nothing to evict. */
  | "all-protected";

/** Result of sliding window compaction. */
export type SlidingWindowResult = {
  messages: SessionMessage[];
  evictedCount: number;
  /** When no messages were evicted, explains why the window returned unchanged. */
  skipReason?: SlidingWindowSkipReason;
};

/**
 * Apply sliding window compaction to messages.
 *
 * Strategy:
 * 1. Always preserve the first system message (if any)
 * 2. Keep the most recent `recencyWindow` message pairs (assistant+user/tool_result)
 * 3. For older messages, evict the least important ones:
 *    - Largest tool_result messages first
 *    - Then older assistant messages
 *    - Never evict system messages
 *
 * Continues evicting until under the token budget.
 */
export function slidingWindowCompact(
  messages: readonly SessionMessage[],
  recencyWindow: number,
  maxSessionTokens: number,
): SlidingWindowResult {
  const totalTokens = estimateSessionTokens(messages);
  if (totalTokens <= maxSessionTokens) {
    return { messages: [...messages], evictedCount: 0, skipReason: "already-under-budget" };
  }
  if (messages.length <= recencyWindow + 1) {
    return { messages: [...messages], evictedCount: 0, skipReason: "too-few-messages" };
  }

  // Separate system messages from the rest
  const systemMessages: SessionMessage[] = [];
  const nonSystemMessages: SessionMessage[] = [];

  for (const msg of messages) {
    if (msg.role === "system") {
      systemMessages.push(msg);
    } else {
      nonSystemMessages.push(msg);
    }
  }

  // Protect recent message pairs
  // A "pair" is typically assistant + user/tool_result
  const recentStart = Math.max(0, nonSystemMessages.length - recencyWindow * 2);
  const recentMessages = nonSystemMessages.slice(recentStart);
  const olderMessages = nonSystemMessages.slice(0, recentStart);

  // If nothing to evict, return as-is
  if (olderMessages.length === 0) {
    return { messages: [...systemMessages, ...nonSystemMessages], evictedCount: 0, skipReason: "all-protected" };
  }

  // Check if we're already within budget with just system + recent
  const systemTokens = estimateSessionTokens(systemMessages);
  const recentTokens = estimateSessionTokens(recentMessages);
  if (systemTokens + recentTokens <= maxSessionTokens) {
    // Recent messages alone fit within budget; evict all older messages
    return { messages: [...systemMessages, ...recentMessages], evictedCount: olderMessages.length };
  }

  // Need to evict from older messages too
  // Sort older messages by "eviction priority" — largest tool_result first
  const evictable = olderMessages
    .map((msg, index) => ({ msg, index }))
    .sort((a, b) => {
      // Tool results with more tokens are evicted first
      if (a.msg.isToolResult && !b.msg.isToolResult) return -1;
      if (!a.msg.isToolResult && b.msg.isToolResult) return 1;
      return b.msg.tokenCount - a.msg.tokenCount;
    });

  // Keep evicting from the end (lowest eviction priority / most important)
  // until we're within budget
  let keptTokens = systemTokens + recentTokens;
  const keptOlder: SessionMessage[] = [];

  // Add older messages back in original order, skipping as needed
  const evictedIndices = new Set<number>();
  let tokensToFree = (systemTokens + estimateSessionTokens(olderMessages) + recentTokens) - maxSessionTokens;

  for (const item of evictable) {
    if (tokensToFree <= 0) break;
    // Evict this message
    evictedIndices.add(item.index);
    tokensToFree -= item.msg.tokenCount;
  }

  // Reconstruct older messages excluding evicted ones
  for (let i = 0; i < olderMessages.length; i++) {
    if (!evictedIndices.has(i)) {
      keptOlder.push(olderMessages[i]);
    }
  }

  const evictedCount = evictedIndices.size;
  return { messages: [...systemMessages, ...keptOlder, ...recentMessages], evictedCount };
}

// ── Compaction Notice ──

/**
 * Create a compaction notice system message explaining what was compressed.
 */
export function compactionNoticeMessage(
  originalCount: number,
  compactedCount: number,
  savedTokens: number,
): SessionMessage {
  const text = [
    `[上下文压缩通知]`,
    `为节省 token，历史会话已自动压缩。`,
    `原始消息数：${originalCount}，压缩后：${compactedCount}，节省约 ${savedTokens} tokens。`,
    `最近的消息保持完整，更早的冗长工具输出已被截断或移除。`,
    `如需查看被压缩的细节，请参考节点执行日志。`,
  ].join("\n");

  return createSystemMessage(text);
}

// ── Helpers ──

/**
 * Build a SessionCompressionStats object.
 */
function buildStats(
  inputMessages: number,
  outputMessages: number,
  inputTokens: number,
  outputTokens: number,
  toolResultsCompressed: number,
  messagesSummarized: number,
  messagesEvicted: number,
  rulesApplied: string[],
  startMs: number,
  wasCompressed: boolean,
  skipReason?: CompactSkipReason,
  compactionLevel?: CompactionLevel,
  circuitBreaker?: CircuitBreakerState,
): SessionCompressionStats {
  return {
    inputMessages,
    outputMessages,
    inputTokens,
    outputTokens,
    compressionRatio: inputTokens > 0 ? outputTokens / inputTokens : 1,
    toolResultsCompressed,
    messagesSummarized,
    messagesEvicted,
    rulesApplied,
    durationMs: Date.now() - startMs,
    wasCompressed,
    skipReason,
    compactionLevel,
    circuitBreaker,
  };
}

// ── Abandoned Session Cleanup ──

/**
 * Clean up an abandoned session file by fixing incomplete terminal turns.
 *
 * When an embedded-agent run ends with `livenessState: "abandoned"`, it means
 * the last assistant turn ended with `stopReason: "toolUse"` — the agent wanted
 * to call a tool but the run was interrupted. The OpenClaw SDK marks such
 * sessions as `replayInvalid: true`, which prevents safe cache reuse on
 * subsequent runs.
 *
 * This function:
 * 1. Reads the session JSONL file line by line
 * 2. Finds the last assistant message with `stopReason: "toolUse"`
 * 3. Replaces its `stopReason` with `"stop"` and appends a closing note
 * 4. Removes trailing orphaned `tool_result` messages (tool results without
 *    a matching assistant tool call, since we changed it to stop)
 * 5. Writes the cleaned file back
 *
 * This makes the session safe for future replay, preserving the cache prefix
 * for subsequent `runEmbeddedPiAgent` calls that reuse this session.
 *
 * @param sessionFile - Path to the session JSONL file
 * @param hasValidOutput - Whether the run produced valid output (skip cleanup if not)
 * @returns true if cleanup was performed, false if session was already clean
 */
export async function cleanupAbandonedSession(
  sessionFile: string,
  hasValidOutput: boolean,
): Promise<boolean> {
  // Don't cleanup if the run produced no valid output — the session is genuinely broken
  if (!hasValidOutput) return false;

  let content: string;
  try {
    content = await readFile(sessionFile, "utf8");
  } catch {
    // File doesn't exist or can't be read — nothing to clean up
    return false;
  }

  const lines = content.split(/\r?\n/).filter((line) => line.trim().length > 0);
  if (lines.length === 0) return false;

  let cleaned = false;
  let lastAbandonedAssistantIndex = -1;

  // Find the last assistant line with stopReason: "toolUse"
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      const parsed = JSON.parse(lines[i]);
      // Check for message-type entries with assistant role and toolUse stopReason
      if (parsed?.type === "message" && parsed?.message?.role === "assistant") {
        const stopReason = parsed.message.stop_reason ?? parsed.message.stopReason;
        if (stopReason === "toolUse" || stopReason === "tool_use") {
          lastAbandonedAssistantIndex = i;
          break;
        }
      }
    } catch {
      // Skip non-JSON lines (shouldn't happen in a valid session file)
      continue;
    }
  }

  if (lastAbandonedAssistantIndex < 0) {
    // No abandoned assistant message found — session is already clean
    console.log(
      `[session-compressor] cleanupAbandonedSession: no abandoned turns found, session is clean`,
    );
    return false;
  }

  console.log(
    `[session-compressor] cleanupAbandonedSession: found abandoned assistant at line ${lastAbandonedAssistantIndex}/${lines.length}, fixing stopReason`,
  );

  // Fix the abandoned assistant message: change stopReason and add closing note
  try {
    const parsed = JSON.parse(lines[lastAbandonedAssistantIndex]);
    const msg = parsed.message;

    // Change stopReason to "stop"
    if ("stop_reason" in msg) {
      msg.stop_reason = "stop";
    }
    if ("stopReason" in msg) {
      msg.stopReason = "stop";
    }

    // Append a closing text block to the assistant content
    const closingNote = "\n[Agent loop completed — pending tool call discarded]";
    if (Array.isArray(msg.content)) {
      msg.content.push({ type: "text", text: closingNote });
    } else if (typeof msg.content === "string") {
      msg.content += closingNote;
    }

    lines[lastAbandonedAssistantIndex] = JSON.stringify(parsed);
    cleaned = true;
  } catch {
    // Can't fix the line — leave it as-is
    return false;
  }

  // Remove trailing orphaned tool_result messages after the fixed assistant.
  // Since we changed stopReason from "toolUse" to "stop", any tool_result
  // messages that follow are orphans (no matching tool call).
  const truncatedLines: string[] = [];
  let seenFixedAssistant = false;

  for (let i = 0; i < lines.length; i++) {
    if (i === lastAbandonedAssistantIndex) {
      seenFixedAssistant = true;
      truncatedLines.push(lines[i]);
      continue;
    }

    if (seenFixedAssistant) {
      // After the fixed assistant, check if this is an orphaned tool_result
      try {
        const parsed = JSON.parse(lines[i]);
        if (parsed?.type === "message" && parsed?.message?.role === "user") {
          // Check if this user message contains tool_result content blocks
          const content = parsed.message.content;
          const hasToolResult = Array.isArray(content)
            && content.some((block: Record<string, unknown>) =>
              block.type === "tool_result" || block.type === "tool_use_result");
          if (hasToolResult) {
            // Skip this orphaned tool_result message
            cleaned = true;
            continue;
          }
        }
      } catch {
        // Can't parse — include it (safer to keep than to lose)
      }
    }

    truncatedLines.push(lines[i]);
  }

  if (cleaned) {
    await writeFile(sessionFile, `${truncatedLines.join("\n")}\n`, "utf8");
    console.log(
      `[session-compressor] cleanupAbandonedSession: session file cleaned, ` +
      `${lines.length} → ${truncatedLines.length} lines`,
    );
  }

  return cleaned;
}