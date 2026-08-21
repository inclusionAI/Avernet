/**
 * Budget Enforcer — enforces budget limits using the strategy configured in FlowBudget.
 *
 * Three strategies are supported:
 * - `hard-stop`: immediately fail the flow when any budget dimension is exhausted.
 * - `graceful-degrade`: switch to a cheaper model when the threshold is reached,
 *   then hard-stop when fully exhausted.
 * - `replan`: trigger a replan when the threshold is reached, allowing the LLM
 *   to adjust its approach. Hard-stop when fully exhausted.
 *
 * The enforcer also emits observability events via the DynamicWorkflowEventEmitter
 * at 80% (warning) and 100% (exhausted) thresholds.
 *
 * @module budget/enforcer
 */

import type { FlowBudget } from "../types.js";
import {
  BudgetTracker,
  type BudgetCheckResult,
  type BudgetThresholdStatus,
} from "./tracker.js";

// ── Enforcement result ──

/** Result of an enforcement check. */
export type EnforcementResult = {
  /** Whether the flow is allowed to continue. */
  allowed: boolean;
  /** The enforcement action taken (if any). */
  action: "none" | "warn" | "degrade" | "replan" | "stop";
  /** Human-readable reason for the action. */
  reason: string;
  /** The budget check result that triggered this enforcement. */
  checkResult: BudgetCheckResult;
  /** For degrade strategy: the fallback model to use. */
  fallbackModel?: string;
  /** Which dimensions triggered the action. */
  triggeredDimensions: Array<"tokens" | "iterations" | "nodes" | "timeout">;
};

// ── Events emitted by the enforcer ──

/** Callback type for emitting budget events to the observability system. */
export type BudgetEventCallback = (
  event: "budget_warning" | "budget_exhausted",
  dimension: string,
  used: number,
  limit: number,
  ratio: number,
) => void;

// ── Enforcer class ──

/**
 * Enforces budget limits for a single flow scope.
 *
 * Usage:
 *   const enforcer = new BudgetEnforcer(budget, eventCallback);
 *   enforcer.tracker.recordTokens(500);
 *   const result = enforcer.enforce(startedAtMs);
 *   if (!result.allowed) { ... stop the flow ... }
 */
export class BudgetEnforcer {
  readonly tracker: BudgetTracker;
  private readonly budget: FlowBudget;
  private readonly onEvent?: BudgetEventCallback;
  private degraded = false;

  constructor(budget: FlowBudget, onEvent?: BudgetEventCallback) {
    this.budget = budget;
    this.tracker = new BudgetTracker(budget);
    this.onEvent = onEvent;
  }

  /**
   * Check budget consumption and enforce limits according to the configured strategy.
   *
   * @param startedAtMs - When the flow/node started (for timeout check).
   * @param nowMs - Current time (for deterministic testing).
   */
  enforce(startedAtMs: number, nowMs?: number): EnforcementResult {
    const checkResult = this.tracker.check(startedAtMs, nowMs);
    const strategy = this.budget.strategy ?? "hard-stop";

    // Collect exhausted dimensions
    const triggeredDimensions: Array<"tokens" | "iterations" | "nodes" | "timeout"> = [];
    if (checkResult.tokens === "exhausted") triggeredDimensions.push("tokens");
    if (checkResult.iterations === "exhausted") triggeredDimensions.push("iterations");
    if (checkResult.nodes === "exhausted") triggeredDimensions.push("nodes");
    if (checkResult.timeout === "exhausted") triggeredDimensions.push("timeout");

    // Collect warning dimensions
    const warningDimensions: Array<"tokens" | "iterations" | "nodes" | "timeout"> = [];
    if (isWarningOrWorse(checkResult.tokens) && checkResult.tokens !== "exhausted") warningDimensions.push("tokens");
    if (isWarningOrWorse(checkResult.iterations) && checkResult.iterations !== "exhausted") warningDimensions.push("iterations");
    if (isWarningOrWorse(checkResult.nodes) && checkResult.nodes !== "exhausted") warningDimensions.push("nodes");
    if (isWarningOrWorse(checkResult.timeout) && checkResult.timeout !== "exhausted") warningDimensions.push("timeout");

    // Emit warning events for dimensions approaching the limit
    for (const dim of warningDimensions) {
      const { used, limit } = getDimensionValues(checkResult, dim);
      const ratio = limit > 0 ? used / limit : 0;
      this.onEvent?.("budget_warning", dim, used, limit, ratio);
    }

    // ── Exhausted: always stop (all strategies converge here) ──
    if (triggeredDimensions.length > 0) {
      for (const dim of triggeredDimensions) {
        const { used, limit } = getDimensionValues(checkResult, dim);
        const ratio = limit > 0 ? used / limit : 0;
        this.onEvent?.("budget_exhausted", dim, used, limit, ratio);
      }

      const reason = `Budget exhausted: ${triggeredDimensions.join(", ")} limit(s) reached.` +
        ` Strategy: ${strategy}. Consumption: tokens=${checkResult.consumption.tokensUsed},` +
        ` iterations=${checkResult.consumption.iterationsUsed},` +
        ` nodes=${checkResult.consumption.nodesInjected},` +
        ` elapsed=${checkResult.consumption.elapsedMs}ms`;

      return {
        allowed: false,
        action: "stop",
        reason,
        checkResult,
        triggeredDimensions,
      };
    }

    // ── Not exhausted but approaching limits ──
    const isCritical = checkResult.overall === "critical";
    const isWarning = checkResult.overall === "warning";

    if (isCritical) {
      switch (strategy) {
        case "graceful-degrade": {
          if (!this.degraded) {
            this.degraded = true;
            const fallbackModel = this.budget.degradeConfig?.fallbackModel ?? "gpt-3.5-turbo";
            return {
              allowed: true,
              action: "degrade",
              reason: `Budget approaching limit (${formatOverallRatio(checkResult)}). ` +
                `Switching to fallback model: ${fallbackModel}.`,
              checkResult,
              fallbackModel,
              triggeredDimensions: [],
            };
          }
          // Already degraded — just warn
          return {
            allowed: true,
            action: "warn",
            reason: `Budget approaching limit (${formatOverallRatio(checkResult)}). Already degraded.`,
            checkResult,
            triggeredDimensions: [],
          };
        }

        case "replan": {
          return {
            allowed: true,
            action: "replan",
            reason: `Budget approaching limit (${formatOverallRatio(checkResult)}). ` +
              `Triggering replan to adjust approach.`,
            checkResult,
            triggeredDimensions: [],
          };
        }

        case "hard-stop":
        default: {
          // hard-stop doesn't stop until exhausted, but warns at critical
          return {
            allowed: true,
            action: "warn",
            reason: `Budget approaching limit (${formatOverallRatio(checkResult)}). ` +
              `Strategy is hard-stop — will stop when exhausted.`,
            checkResult,
            triggeredDimensions: [],
          };
        }
      }
    }

    if (isWarning) {
      return {
        allowed: true,
        action: "warn",
        reason: `Budget warning: ${formatOverallRatio(checkResult)} consumed.`,
        checkResult,
        triggeredDimensions: [],
      };
    }

    // All clear
    return {
      allowed: true,
      action: "none",
      reason: "Budget OK.",
      checkResult,
      triggeredDimensions: [],
    };
  }

  /** Whether the enforcer has already switched to the degraded model. */
  isDegraded(): boolean {
    return this.degraded;
  }
}

// ── Helpers ──

function isWarningOrWorse(status: BudgetThresholdStatus): boolean {
  return status === "warning" || status === "critical" || status === "exhausted";
}

function getDimensionValues(
  result: BudgetCheckResult,
  dim: "tokens" | "iterations" | "nodes" | "timeout",
): { used: number; limit: number } {
  const limits = result.limits;
  switch (dim) {
    case "tokens":
      return { used: result.consumption.tokensUsed, limit: limits.maxTokens ?? 0 };
    case "iterations":
      return { used: result.consumption.iterationsUsed, limit: limits.maxIterations ?? 0 };
    case "nodes":
      return { used: result.consumption.nodesInjected, limit: limits.maxNodes ?? 0 };
    case "timeout":
      return { used: result.consumption.elapsedMs, limit: limits.timeoutMs ?? 0 };
  }
}

function formatOverallRatio(result: BudgetCheckResult): string {
  const limits = result.limits;
  const parts: string[] = [];
  if (limits.maxTokens) parts.push(`tokens ${Math.round(result.consumption.tokensUsed / limits.maxTokens * 100)}%`);
  if (limits.maxIterations) parts.push(`iterations ${Math.round(result.consumption.iterationsUsed / limits.maxIterations * 100)}%`);
  if (limits.maxNodes) parts.push(`nodes ${Math.round(result.consumption.nodesInjected / limits.maxNodes * 100)}%`);
  if (limits.timeoutMs) parts.push(`time ${Math.round(result.consumption.elapsedMs / limits.timeoutMs * 100)}%`);
  return parts.join(", ") || "N/A";
}