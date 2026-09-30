/**
 * Human approval gate for synthesized workflows.
 *
 * After synthesis + validation succeeds, the human gate determines
 * whether the synthesized YAML requires human approval before execution.
 *
 * Three strategies:
 * - "never"    → Skip approval entirely
 * - "on-warning" → Require approval only when warning triggers fire
 * - "always"   → Always require approval
 *
 * Warning triggers:
 * - nodeCountExceeds: generated workflow has more nodes than threshold
 * - budgetExceeds: token consumption exceeds threshold
 * - usesDynamicExecutors: workflow uses dynamic-template, goal-evaluator, or llm-orchestrator
 * - hasLoopGroup: workflow contains a loop-group node
 */
import type { SynthesisConfig, WorkflowSpec } from "../types.js";
import type { DynamicWorkflowEventEmitter } from "../observability/emitter.js";

// ── Public types ──

export type HumanGateDecision = {
  /** Whether human approval is needed before execution. */
  needsApproval: boolean;
  /** List of triggered warnings that caused the approval requirement. */
  triggeredWarnings: string[];
  /** Human-readable reason for the decision. */
  reason: string;
};

// ── Public API ──

/**
 * Check whether a synthesized workflow requires human approval.
 *
 * When an emitter is provided and approval is needed, emits a
 * `human_approval_requested` observability event.
 */
export function checkHumanApprovalNeeded(
  spec: WorkflowSpec,
  config: SynthesisConfig,
  tokenUsage?: { totalTokens: number },
  observability?: {
    emitter: DynamicWorkflowEventEmitter;
    flowId: string;
    workflowId: string;
  },
): HumanGateDecision {
  const { strategy, warningTriggers } = config.humanApproval;

  // Strategy: never → always skip
  if (strategy === "never") {
    return {
      needsApproval: false,
      triggeredWarnings: [],
      reason: "Approval strategy is 'never' — skipping human approval",
    };
  }

  // Evaluate warning triggers
  const triggeredWarnings: string[] = [];

  // 1. nodeCountExceeds
  if (warningTriggers.nodeCountExceeds > 0 && spec.nodes.length > warningTriggers.nodeCountExceeds) {
    triggeredWarnings.push(
      `Node count (${spec.nodes.length}) exceeds threshold (${warningTriggers.nodeCountExceeds})`,
    );
  }

  // 2. budgetExceeds
  if (
    warningTriggers.budgetExceeds > 0 &&
    tokenUsage &&
    tokenUsage.totalTokens > warningTriggers.budgetExceeds
  ) {
    triggeredWarnings.push(
      `Token usage (${tokenUsage.totalTokens}) exceeds threshold (${warningTriggers.budgetExceeds})`,
    );
  }

  // 3. usesDynamicExecutors
  if (warningTriggers.usesDynamicExecutors) {
    const dynamicTypes = new Set(["dynamic-template", "goal-evaluator", "llm-orchestrator"]);
    const hasDynamic = spec.nodes.some((n) => {
      const execType = n.executor?.type;
      return execType && dynamicTypes.has(execType);
    });
    if (hasDynamic) {
      triggeredWarnings.push("Workflow uses dynamic executors (dynamic-template, goal-evaluator, or llm-orchestrator)");
    }
  }

  // 4. hasLoopGroup
  if (warningTriggers.hasLoopGroup) {
    const hasLoop = spec.nodes.some(
      (n) => n.executor?.type === "loop-group",
    );
    if (hasLoop) {
      triggeredWarnings.push("Workflow contains a loop-group node");
    }
  }

  // Strategy: always → always require approval
  if (strategy === "always") {
    const decision: HumanGateDecision = {
      needsApproval: true,
      triggeredWarnings,
      reason: triggeredWarnings.length > 0
        ? `Approval strategy is 'always' and warnings detected: ${triggeredWarnings.join("; ")}`
        : "Approval strategy is 'always' — human approval required regardless of warnings",
    };
    if (observability) {
      observability.emitter.emitHumanApprovalRequested(
        observability.flowId, observability.workflowId, "human-gate",
        { strategy, triggeredWarnings: decision.triggeredWarnings, reason: decision.reason },
      ).catch(() => { /* best-effort */ });
    }
    return decision;
  }

  // Strategy: on-warning → require approval if any warnings triggered
  if (strategy === "on-warning") {
    if (triggeredWarnings.length > 0) {
      const decision: HumanGateDecision = {
        needsApproval: true,
        triggeredWarnings,
        reason: `Warnings triggered: ${triggeredWarnings.join("; ")}`,
      };
      if (observability) {
        observability.emitter.emitHumanApprovalRequested(
          observability.flowId, observability.workflowId, "human-gate",
          { strategy, triggeredWarnings: decision.triggeredWarnings, reason: decision.reason },
        ).catch(() => { /* best-effort */ });
      }
      return decision;
    }
    return {
      needsApproval: false,
      triggeredWarnings: [],
      reason: "No warning triggers fired — approval not required",
    };
  }

  // Unknown strategy — be conservative, require approval
  const fallbackDecision: HumanGateDecision = {
    needsApproval: true,
    triggeredWarnings,
    reason: `Unknown approval strategy '${strategy}' — requiring approval as a safety measure`,
  };
  if (observability) {
    observability.emitter.emitHumanApprovalRequested(
      observability.flowId, observability.workflowId, "human-gate",
      { strategy, triggeredWarnings: fallbackDecision.triggeredWarnings, reason: fallbackDecision.reason },
    ).catch(() => { /* best-effort */ });
  }
  return fallbackDecision;
}