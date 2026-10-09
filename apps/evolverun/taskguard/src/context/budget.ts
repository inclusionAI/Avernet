/**
 * Token budget management for context compression.
 *
 * Manages warning and hard-limit thresholds, and determines when
 * compression should be triggered.
 *
 * @module context/budget
 */

import type { ContextCompressionConfig, TokenBudget, ContextCompressionDefaults } from "./types.js";
import { COMPRESSION_DEFAULTS } from "./types.js";

/**
 * Resolves the effective token budget from per-node config and global defaults.
 *
 * Merge rule: per-node config wins over global defaults.
 * For `steps` arrays, the node-level list entirely replaces the default list.
 */
export function resolveTokenBudget(
  config: ContextCompressionConfig | undefined,
  defaults: ContextCompressionDefaults | undefined,
): TokenBudget {
  const global = defaults ?? COMPRESSION_DEFAULTS;
  const maxTokens = config?.budget?.maxTokens ?? global.defaultMaxTokens;
  const warningThreshold = config?.budget?.warningThreshold
    ?? Math.round(maxTokens * global.warningThresholdRatio);
  const overflowStrategy = config?.budget?.overflowStrategy ?? global.defaultOverflowStrategy;
  return { maxTokens, warningThreshold, overflowStrategy };
}

/**
 * Token budget manager — checks whether context is over warning or hard-limit thresholds.
 */
export class TokenBudgetManager {
  readonly maxTokens: number;
  readonly warningThreshold: number;
  readonly overflowStrategy: string;

  constructor(budget: TokenBudget) {
    this.maxTokens = budget.maxTokens;
    this.warningThreshold = budget.warningThreshold ?? Math.round(budget.maxTokens * 0.7);
    this.overflowStrategy = budget.overflowStrategy ?? "priority-evict";
  }

  /** Check if a token count exceeds the warning threshold. */
  isOverWarning(currentTokens: number): boolean {
    return currentTokens > this.warningThreshold;
  }

  /** Check if a token count exceeds the hard limit. */
  isOverBudget(currentTokens: number): boolean {
    return currentTokens > this.maxTokens;
  }

  /** Tokens that need to be reduced to fit within budget. */
  excessTokens(currentTokens: number): number {
    return Math.max(0, currentTokens - this.maxTokens);
  }

  /** Target token count after compression (97% of budget to leave some headroom). */
  targetTokens(): number {
    return Math.floor(this.maxTokens * 0.97);
  }
}