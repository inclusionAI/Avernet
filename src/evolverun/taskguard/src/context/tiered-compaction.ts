/**
 * Tiered compaction strategy and circuit breaker for session compression.
 *
 * Inspired by Claw Compactor's TieredCompaction: selects the appropriate
 * compaction level based on context pressure (micro/auto/full) and applies
 * progressively more aggressive compression strategies.
 *
 * Levels:
 * - none:  Context is under 60% of budget — no compression needed
 * - micro: Context is 60-80% of budget — lightweight, zero-cost operations only
 *           (tool result budget truncation by age)
 * - auto:  Context is 80-95% of budget — medium compression
 *           (micro + cross-message dedup + conversation summarization)
 * - full:  Context is >95% of budget — aggressive compression
 *           (auto + content-aware prepass rules + sliding window eviction)
 *
 * Also includes a CircuitBreaker that disables compaction after
 * MAX_CONSECUTIVE_FAILURES consecutive failures, preventing infinite
 * retry loops.
 *
 * @module context/tiered-compaction
 */

import type { SessionMessage } from "./session-reader.js";
import { estimateSessionTokens } from "./session-reader.js";
import { budgetToolResults, type ToolResultBudgetOptions, type ToolResultBudgetResult } from "./tool-result-budget.js";
import { deduplicateMessages, type SemanticDedupResult } from "./semantic-dedup.js";
import { summarizeOldTurns, type SummarizeOptions, type SummarizeResult } from "./conversation-summarizer.js";

// ── Types ──

/** Compaction aggressiveness levels, ordered by intensity. */
export type CompactionLevel = "none" | "micro" | "auto" | "full";

/** Thresholds for each compaction level (fraction of context budget). */
export type CompactionThresholds = {
  /** Fraction of budget at which micro compaction triggers. Default: 0.60 */
  micro: number;
  /** Fraction of budget at which auto compaction triggers. Default: 0.80 */
  auto: number;
  /** Fraction of budget at which full compaction triggers. Default: 0.95 */
  full: number;
};

/** Result of tiered compaction. */
export type TieredCompactionResult = {
  /** Messages after compaction (new array, no mutation). */
  messages: SessionMessage[];
  /** The compaction level that was applied. */
  level: CompactionLevel;
  /** Whether any compaction actually reduced tokens. */
  wasCompressed: boolean;
  /** Per-stage results. */
  stages: CompactionStages;
  /** Circuit breaker state after compaction. */
  circuitBreaker: CircuitBreakerState;
  /** Total tokens before compaction. */
  inputTokens: number;
  /** Total tokens after compaction. */
  outputTokens: number;
};

/** Per-stage compression results. */
export type CompactionStages = {
  /** Tool result budget truncation result (micro+ stages). */
  budget?: ToolResultBudgetResult;
  /** Semantic deduplication result (auto+ stages). */
  dedup?: SemanticDedupResult;
  /** Conversation summarization result (auto+ stages). */
  summary?: SummarizeResult;
  /** Prepass compression stats (if applied externally). */
  prepassApplied?: boolean;
  /** Sliding window eviction count (if applied externally). */
  windowEvictedCount?: number;
};

/** Serialized circuit breaker state. */
export type CircuitBreakerState = {
  consecutiveFailures: number;
  disabled: boolean;
  totalAttempts: number;
  totalFailures: number;
};

// ── Constants ──

/** Default thresholds for compaction level selection. */
export const DEFAULT_THRESHOLDS: CompactionThresholds = {
  micro: 0.60,
  auto: 0.80,
  full: 0.95,
};

/** Maximum consecutive failures before the circuit breaker trips. */
const MAX_CONSECUTIVE_FAILURES = 3;

// ── Circuit Breaker ──

/**
 * Circuit breaker that disables compaction after too many consecutive failures.
 *
 * Prevents infinite retry loops — a bug that wasted 250K API calls/day
 * in a similar system (Claw Code's AutoCompact).
 */
export class CircuitBreaker {
  private consecutiveFailures = 0;
  private totalAttempts = 0;
  private totalFailures = 0;
  private _disabled = false;
  private readonly maxFailures: number;

  constructor(maxFailures: number = MAX_CONSECUTIVE_FAILURES) {
    this.maxFailures = maxFailures;
  }

  /** Whether the circuit breaker has tripped and disabled compaction. */
  get disabled(): boolean {
    return this._disabled;
  }

  /** Record a successful compaction. Resets consecutive failure count. */
  recordSuccess(): void {
    this.consecutiveFailures = 0;
    this.totalAttempts++;
  }

  /** Record a compaction failure. Trips the breaker if threshold reached. */
  recordFailure(): void {
    this.consecutiveFailures++;
    this.totalFailures++;
    this.totalAttempts++;
    if (this.consecutiveFailures >= this.maxFailures) {
      this._disabled = true;
    }
  }

  /** Reset the circuit breaker (e.g., at the start of a new session). */
  reset(): void {
    this.consecutiveFailures = 0;
    this._disabled = false;
  }

  /** Get the current state for observability. */
  getState(): CircuitBreakerState {
    return {
      consecutiveFailures: this.consecutiveFailures,
      disabled: this._disabled,
      totalAttempts: this.totalAttempts,
      totalFailures: this.totalFailures,
    };
  }
}

// ── Level Determination ──

/**
 * Determine the compaction level based on current token usage vs budget.
 *
 * @param sessionTokens - Current estimated token count of the session
 * @param effectiveBudget - The effective token budget (accounting for non-session overhead)
 * @param thresholds - Optional custom thresholds
 * @returns The recommended compaction level
 */
export function determineCompactionLevel(
  sessionTokens: number,
  effectiveBudget: number,
  thresholds?: Partial<CompactionThresholds>,
): CompactionLevel {
  const t = { ...DEFAULT_THRESHOLDS, ...thresholds };
  // Zero budget with any tokens → full compaction (can't fit anything)
  // Zero tokens → no compaction needed
  if (effectiveBudget <= 0) {
    return sessionTokens > 0 ? "full" : "none";
  }
  const ratio = sessionTokens / effectiveBudget;

  if (ratio >= t.full) return "full";
  if (ratio >= t.auto) return "auto";
  if (ratio >= t.micro) return "micro";
  return "none";
}

// ── Tiered Compaction ──

/** Options for applyTieredCompaction. */
export type TieredCompactionOptions = {
  /** Effective token budget for the session. */
  effectiveBudget: number;
  /** Custom compaction thresholds. */
  thresholds?: Partial<CompactionThresholds>;
  /** Tool result budget options (for micro+ stages). */
  budgetOptions?: ToolResultBudgetOptions;
  /** Summarization options (for auto+ stages). */
  summarizeOptions?: SummarizeOptions;
  /** SimHash Hamming distance threshold for near-duplicate detection. Default: 3 */
  dedupThreshold?: number;
  /** Custom compaction level override (skips auto-detection). */
  levelOverride?: CompactionLevel;
  /** Circuit breaker instance (created if not provided). */
  circuitBreaker?: CircuitBreaker;
};

/**
 * Apply tiered compaction to a list of session messages.
 *
 * Based on the compaction level, applies progressively more aggressive
 * compression stages:
 *
 * - **micro**: Budget truncation of old tool results only
 * - **auto**:  micro + cross-message dedup + conversation summarization
 * - **full**:  auto + (prepass and sliding window are applied externally by compactSession)
 *
 * Returns a new messages array (immutable — input is not mutated).
 * The "full" stage is expected to be combined with the existing tool-output-prepass
 * and sliding-window eviction in compactSession.
 */
export function applyTieredCompaction(
  messages: readonly SessionMessage[],
  options: TieredCompactionOptions,
): TieredCompactionResult {
  const breaker = options.circuitBreaker ?? new CircuitBreaker();
  const inputTokens = estimateSessionTokens(messages);

  // Check circuit breaker
  if (breaker.disabled) {
    return {
      messages: [...messages],
      level: "none",
      wasCompressed: false,
      stages: {},
      circuitBreaker: breaker.getState(),
      inputTokens,
      outputTokens: inputTokens,
    };
  }

  // Determine compaction level
  const level = options.levelOverride ??
    determineCompactionLevel(inputTokens, options.effectiveBudget, options.thresholds);

  if (level === "none") {
    return {
      messages: [...messages],
      level: "none",
      wasCompressed: false,
      stages: {},
      circuitBreaker: breaker.getState(),
      inputTokens,
      outputTokens: inputTokens,
    };
  }

  try {
    let current: SessionMessage[] = [...messages];
    const stages: CompactionStages = {};

    // ── Stage 1: Micro — tool result budget truncation ──
    if (level === "micro" || level === "auto" || level === "full") {
      const budgetResult = budgetToolResults(current, options.budgetOptions);
      current = budgetResult.messages;
      stages.budget = budgetResult;

      console.log(
        `[tiered-compaction] micro budget: truncated=${budgetResult.truncatedCount}, ` +
        `oversized=${budgetResult.oversizedCount}, saved=${budgetResult.tokensSaved} tokens`,
      );
    }

    // ── Stage 2: Auto — cross-message dedup + conversation summarization ──
    if (level === "auto" || level === "full") {
      // Cross-message semantic dedup
      const dedupResult = deduplicateMessages(current, options.dedupThreshold);
      current = dedupResult.messages;
      stages.dedup = dedupResult;

      console.log(
        `[tiered-compaction] auto dedup: deduped=${dedupResult.dedupedCount}, ` +
        `saved=${dedupResult.tokensSaved} tokens`,
      );

      // Conversation summarization of old turns
      const summaryResult = summarizeOldTurns(current, options.summarizeOptions);
      if (summaryResult.triggered) {
        current = summaryResult.messages;
        stages.summary = summaryResult;

        console.log(
          `[tiered-compaction] auto summary: summarized=${summaryResult.turnsSummarized} turns, ` +
          `summaryTokens=${summaryResult.summaryTokens}`,
        );
      }
    }

    // ── Stage 3: Full — content-aware prepass + sliding window ──
    // (these are applied by compactSession itself, not here)
    if (level === "full") {
      stages.prepassApplied = false; // Will be set by compactSession
      stages.windowEvictedCount = 0;  // Will be set by compactSession
    }

    const outputTokens = estimateSessionTokens(current);
    const wasCompressed = outputTokens < inputTokens;

    if (wasCompressed) {
      breaker.recordSuccess();
    } else {
      // No compression happened — not necessarily a failure
      breaker.recordSuccess();
    }

    return {
      messages: current,
      level,
      wasCompressed,
      stages,
      circuitBreaker: breaker.getState(),
      inputTokens,
      outputTokens,
    };
  } catch (err) {
    breaker.recordFailure();

    console.error(
      `[tiered-compaction] compaction failed at level ${level}:`,
      err instanceof Error ? err.message : String(err),
    );

    return {
      messages: [...messages],
      level,
      wasCompressed: false,
      stages: {},
      circuitBreaker: breaker.getState(),
      inputTokens,
      outputTokens: inputTokens,
    };
  }
}