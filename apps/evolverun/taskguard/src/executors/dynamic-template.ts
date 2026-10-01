/**
 * Dynamic-template executor — NEVER executed directly.
 *
 * Like `loop-group`, a `dynamic-template` node is expanded by the Controller
 * into per-item runtime nodes before execution. If this executor is reached
 * through the dispatch switch, it means expansion did not happen — which is
 * a bug. Return a failed result to surface this early.
 */
import type { WorkflowNode, ExecutorResult } from "../types.js";
import type { TemplateContext } from "../runner.js";

export async function executeDynamicTemplate(
  node: WorkflowNode,
  _templateCtx: TemplateContext,
): Promise<ExecutorResult> {
  return {
    status: "failed",
    error: `dynamic-template node "${node.id}" must be materialized by controller before execution`,
  };
}