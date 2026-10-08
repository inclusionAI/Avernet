/**
 * Budget observability bridge — connects the BudgetEnforcer to the
 * DynamicWorkflowEventEmitter so threshold events are persisted and
 * broadcast in real-time.
 *
 * @module budget/observability
 */

import type { DynamicWorkflowEventEmitter } from "../observability/emitter.js";
import type { BudgetEventCallback } from "./enforcer.js";

/**
 * Create a BudgetEventCallback that forwards events to the
 * DynamicWorkflowEventEmitter.
 *
 * Usage:
 *   const callback = createBudgetEventCallback(emitter, flowId, workflowId, nodeId);
 *   const enforcer = new BudgetEnforcer(budget, callback);
 */
export function createBudgetEventCallback(
  emitter: DynamicWorkflowEventEmitter,
  flowId: string,
  workflowId: string,
  nodeId: string,
): BudgetEventCallback {
  return (event, dimension, used, limit, ratio) => {
    const budgetType = dimension; // tokens, iterations, nodes, timeout
    const data = { budgetType, used, limit, ratio };

    if (event === "budget_warning") {
      emitter.emitBudgetWarning(flowId, workflowId, nodeId, data).catch(() => { /* best-effort */ });
    } else if (event === "budget_exhausted") {
      emitter.emitBudgetExhausted(flowId, workflowId, nodeId, data).catch(() => { /* best-effort */ });
    }
  };
}