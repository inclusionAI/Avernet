/**
 * Budget Tracker — per-flow and per-node consumption tracking.
 *
 * Tracks tokens, iterations, node count, and elapsed time against
 * the budget limits defined in WorkflowSpec.budget or per-node budget.
 *
 * The tracker is intentionally stateless regarding enforcement —
 * it only computes consumption and threshold status. Enforcement
 * decisions are handled by the Enforcer (src/budget/enforcer.ts).
 *
 * All methods are pure and synchronous — no side effects.
 *
 * @module budget/tracker
 */

import type { FlowBudget } from "../types.js";

// ── Consumption snapshot ──

/** Current consumption metrics for a flow or node. */
export type BudgetConsumption = {
  /** Tokens consumed so far (from LLM calls). */
  tokensUsed: number;
  /** Iterations completed so far (for orchestrator nodes). */
  iterationsUsed: number;
  /** Dynamically injected nodes created so far. */
  nodesInjected: number;
  /** Elapsed wall-clock time in ms since the flow/node started. */
  elapsedMs: number;
};

/** Threshold status for a single budget dimension. */
export type BudgetThresholdStatus =
  | "ok"
  | "warning"   // ≥ 80% of limit
  | "critical"  // ≥ 90% of limit
  | "exhausted"; // ≥ 100% of limit

/** Full budget check result across all dimensions. */
export type BudgetCheckResult = {
  /** Per-dimension threshold status. */
  tokens: BudgetThresholdStatus;
  iterations: BudgetThresholdStatus;
  nodes: BudgetThresholdStatus;
  timeout: BudgetThresholdStatus;
  /** Overall status — the worst status across all dimensions. */
  overall: BudgetThresholdStatus;
  /** Consumption snapshot at the time of the check. */
  consumption: BudgetConsumption;
  /** The budget limits that were checked (from FlowBudget). */
  limits: FlowBudget;
};

// ── Threshold constants ──

const WARNING_RATIO = 0.8;
const CRITICAL_RATIO = 0.9;

// ── BudgetTracker class ──

/**
 * Tracks budget consumption for a single flow or node scope.
 *
 * Usage:
 *   const tracker = new BudgetTracker(budget);
 *   tracker.recordTokens(500);
 *   const result = tracker.check(startedAtMs);
 *   if (result.overall === "exhausted") { ... }
 */
export class BudgetTracker {
  private tokensUsed = 0;
  private iterationsUsed = 0;
  private nodesInjected = 0;
  private readonly limits: FlowBudget;

  constructor(budget: FlowBudget) {
    this.limits = budget;
  }

  // ── Mutations (immutable return of new consumption) ──

  /** Record token consumption from an LLM call. */
  recordTokens(count: number): void {
    this.tokensUsed += count;
  }

  /** Record one iteration completed (e.g., orchestrator loop). */
  recordIteration(): void {
    this.iterationsUsed += 1;
  }

  /** Record one node dynamically injected into the DAG. */
  recordInjectedNode(count = 1): void {
    this.nodesInjected += count;
  }

  // ── Queries (pure, no side effects) ──

  /** Get current consumption snapshot. */
  getConsumption(nowMs?: number): BudgetConsumption {
    return {
      tokensUsed: this.tokensUsed,
      iterationsUsed: this.iterationsUsed,
      nodesInjected: this.nodesInjected,
      elapsedMs: nowMs ?? Date.now(),
    };
  }

  /** Check threshold status across all budget dimensions. */
  check(startedAtMs: number, nowMs?: number): BudgetCheckResult {
    const now = nowMs ?? Date.now();
    const elapsed = now - startedAtMs;

    const consumption: BudgetConsumption = {
      tokensUsed: this.tokensUsed,
      iterationsUsed: this.iterationsUsed,
      nodesInjected: this.nodesInjected,
      elapsedMs: elapsed,
    };

    const tokens = checkDimension(this.tokensUsed, this.limits.maxTokens);
    const iterations = checkDimension(this.iterationsUsed, this.limits.maxIterations);
    const nodes = checkDimension(this.nodesInjected, this.limits.maxNodes);
    const timeout = checkDimension(elapsed, this.limits.timeoutMs);

    const overall = worstStatus(tokens, iterations, nodes, timeout);

    return {
      tokens,
      iterations,
      nodes,
      timeout,
      overall,
      consumption,
      limits: this.limits,
    };
  }

  /** Check only the token budget dimension. Returns threshold status. */
  checkTokens(): BudgetThresholdStatus {
    return checkDimension(this.tokensUsed, this.limits.maxTokens);
  }

  /** Check only the iteration budget dimension. Returns threshold status. */
  checkIterations(): BudgetThresholdStatus {
    return checkDimension(this.iterationsUsed, this.limits.maxIterations);
  }

  /** Check only the node count budget dimension. Returns threshold status. */
  checkNodes(): BudgetThresholdStatus {
    return checkDimension(this.nodesInjected, this.limits.maxNodes);
  }

  /** Check only the timeout dimension. */
  checkTimeout(startedAtMs: number, nowMs?: number): BudgetThresholdStatus {
    const elapsed = (nowMs ?? Date.now()) - startedAtMs;
    return checkDimension(elapsed, this.limits.timeoutMs);
  }

  /** Get the budget limits. */
  getLimits(): FlowBudget {
    return this.limits;
  }

  /** Is the budget configured with at least one limit? */
  hasLimits(): boolean {
    return (
      this.limits.maxTokens !== undefined ||
      this.limits.maxIterations !== undefined ||
      this.limits.maxNodes !== undefined ||
      this.limits.timeoutMs !== undefined
    );
  }
}

// ── Helpers ──

/**
 * Check a single consumption value against its limit.
 * Returns the threshold status: ok / warning / critical / exhausted.
 * If no limit is defined, always returns "ok".
 */
function checkDimension(used: number, limit: number | undefined): BudgetThresholdStatus {
  if (limit === undefined || limit <= 0) return "ok";
  const ratio = used / limit;
  if (ratio >= 1.0) return "exhausted";
  if (ratio >= CRITICAL_RATIO) return "critical";
  if (ratio >= WARNING_RATIO) return "warning";
  return "ok";
}

/**
 * Return the worst status among the given statuses.
 * Priority: exhausted > critical > warning > ok
 */
function worstStatus(...statuses: BudgetThresholdStatus[]): BudgetThresholdStatus {
  const order: Record<BudgetThresholdStatus, number> = {
    ok: 0,
    warning: 1,
    critical: 2,
    exhausted: 3,
  };
  let worst: BudgetThresholdStatus = "ok";
  for (const s of statuses) {
    if (order[s] > order[worst]) {
      worst = s;
    }
  }
  return worst;
}

/**
 * Create a no-op budget tracker that always returns "ok".
 * Used when no budget is configured.
 */
export function createNoOpBudgetTracker(): BudgetTracker {
  return new BudgetTracker({});
}