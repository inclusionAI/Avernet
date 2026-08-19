/**
 * Convergence detector — determines whether the goal-loop should continue
 * or stop based on iteration history, budget consumption, and configuration.
 *
 * Five stop conditions:
 * 1. no-progress: consecutive N iterations with same failure reason
 * 2. repeated-failure: same nodeId fails N times consecutively
 * 3. budget-exhausted: tokens / iterations / nodes exceeded
 * 4. max-iterations: currentIteration exceeds maxIterations
 * 5. max-replans: replans exceeds maxReplans
 *
 * @module goal-loop/convergence-detector
 */

import type {
  ConvergenceStatus,
  GoalLoopIterationRecord,
  GoalLoopRuntimeState,
  GoalLoopExecutor,
  FlowBudget,
} from "../types.js";

// ── Public API ──

/**
 * Check whether the goal-loop should continue or stop.
 *
 * Called after each iteration to decide if the loop has converged
 * or should be terminated.
 */
export function checkConvergence(
  state: GoalLoopRuntimeState,
  config: Pick<GoalLoopExecutor, "maxIterations" | "budget" | "convergence" | "repair">,
): ConvergenceStatus {
  // 1. max-iterations
  const maxIterations = config.maxIterations ?? 20;
  if (state.currentIteration >= maxIterations) {
    return { status: "stop", reason: "max-iterations" };
  }

  // 2. budget-exhausted
  const budgetCheck = checkBudget(state, config.budget);
  if (budgetCheck) return budgetCheck;

  // 3. max-replans
  const maxReplans = config.repair?.maxReplans ?? 3;
  if (state.replans >= maxReplans) {
    return { status: "stop", reason: "max-replans" };
  }

  // 4. no-progress
  const noProgressThreshold = config.convergence?.noProgressIterations ?? 3;
  const noProgressCheck = checkNoProgress(state.iterations, noProgressThreshold);
  if (noProgressCheck) return noProgressCheck;

  // 5. repeated-failure
  const repeatedFailureThreshold = config.convergence?.repeatedFailures ?? 5;
  const repeatedCheck = checkRepeatedFailures(state.iterations, repeatedFailureThreshold);
  if (repeatedCheck) return repeatedCheck;

  return { status: "continue" };
}

// ── Internal checks ──

function checkBudget(
  state: GoalLoopRuntimeState,
  budget?: FlowBudget,
): ConvergenceStatus | null {
  if (!budget) return null;

  // tokens
  if (budget.maxTokens !== undefined && state.budgetUsed.tokens >= budget.maxTokens) {
    return { status: "stop", reason: "budget-exhausted" };
  }

  // iterations (from budget, not maxIterations)
  if (budget.maxIterations !== undefined && state.budgetUsed.iterations >= budget.maxIterations) {
    return { status: "stop", reason: "budget-exhausted" };
  }

  // nodes
  if (budget.maxNodes !== undefined && state.budgetUsed.nodes >= budget.maxNodes) {
    return { status: "stop", reason: "budget-exhausted" };
  }

  return null;
}

function checkNoProgress(
  iterations: GoalLoopIterationRecord[],
  threshold: number,
): ConvergenceStatus | null {
  if (iterations.length < threshold) return null;

  // Get the last N iterations that have a failureReason
  const recentWithFailures = iterations.slice(-threshold);
  const allFailed = recentWithFailures.every((iter) => iter.failureReason !== undefined);
  if (!allFailed) return null;

  // Check if all failure reasons are the same
  const firstReason = recentWithFailures[0].failureReason;
  const allSameReason = recentWithFailures.every(
    (iter) => iter.failureReason === firstReason,
  );

  if (allSameReason && firstReason) {
    return { status: "stop", reason: "no-progress" };
  }

  return null;
}

function checkRepeatedFailures(
  iterations: GoalLoopIterationRecord[],
  threshold: number,
): ConvergenceStatus | null {
  if (iterations.length === 0) return null;

  // Track consecutive failures by nodeId — count backwards from the end
  // until we hit a non-failed iteration or a different nodeId
  let consecutiveCount = 0;
  let lastFailedNodeId: string | undefined;

  for (let i = iterations.length - 1; i >= 0; i--) {
    const iter = iterations[i];
    if (iter.failedNodeId === undefined || iter.failureReason === undefined) {
      // Hit a non-failed iteration — stop counting
      break;
    }
    if (lastFailedNodeId === undefined) {
      lastFailedNodeId = iter.failedNodeId;
      consecutiveCount = 1;
    } else if (iter.failedNodeId === lastFailedNodeId) {
      consecutiveCount++;
    } else {
      // Different node failed — stop counting
      break;
    }
  }

  if (consecutiveCount >= threshold && lastFailedNodeId) {
    return { status: "stop", reason: "repeated-failure" };
  }

  return null;
}

// ── Budget threshold helpers (for observability) ──

export function getBudgetThresholdStatus(
  state: GoalLoopRuntimeState,
  budget?: FlowBudget,
): {
  tokens?: { used: number; limit: number; percentage: number; status: "ok" | "warning" | "exhausted" };
  iterations?: { used: number; limit: number; percentage: number; status: "ok" | "warning" | "exhausted" };
  nodes?: { used: number; limit: number; percentage: number; status: "ok" | "warning" | "exhausted" };
} {
  if (!budget) return {};

  const result: Record<string, unknown> = {};

  if (budget.maxTokens !== undefined) {
    const pct = (state.budgetUsed.tokens / budget.maxTokens) * 100;
    result.tokens = {
      used: state.budgetUsed.tokens,
      limit: budget.maxTokens,
      percentage: Math.round(pct),
      status: pct >= 100 ? "exhausted" : pct >= 80 ? "warning" : "ok",
    };
  }

  if (budget.maxIterations !== undefined) {
    const pct = (state.budgetUsed.iterations / budget.maxIterations) * 100;
    result.iterations = {
      used: state.budgetUsed.iterations,
      limit: budget.maxIterations,
      percentage: Math.round(pct),
      status: pct >= 100 ? "exhausted" : pct >= 80 ? "warning" : "ok",
    };
  }

  if (budget.maxNodes !== undefined) {
    const pct = (state.budgetUsed.nodes / budget.maxNodes) * 100;
    result.nodes = {
      used: state.budgetUsed.nodes,
      limit: budget.maxNodes,
      percentage: Math.round(pct),
      status: pct >= 100 ? "exhausted" : pct >= 80 ? "warning" : "ok",
    };
  }

  return result as {
    tokens?: { used: number; limit: number; percentage: number; status: "ok" | "warning" | "exhausted" };
    iterations?: { used: number; limit: number; percentage: number; status: "ok" | "warning" | "exhausted" };
    nodes?: { used: number; limit: number; percentage: number; status: "ok" | "warning" | "exhausted" };
  };
}