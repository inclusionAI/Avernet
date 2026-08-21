/**
 * Repair strategies — apply a GuardianRepair to a WorkflowNode,
 * producing an effective node for the next retry attempt.
 *
 * The repair is applied in-memory only; the original workflow spec
 * is never modified. If the repair is invalid or unsafe, it falls
 * back to the original node.
 */
import type { WorkflowNode, WorkflowSpec } from "../types.js";
import type { GuardianRepair } from "./types.js";

/**
 * Apply a repair strategy to a node, returning a new effective node
 * for the next retry. The original node is not mutated.
 */
export function applyRepair(
  node: WorkflowNode,
  repair: GuardianRepair,
  maxPromptMultiplier: number = 2,
): WorkflowNode {
  if (repair.action === "retry-as-is" || repair.action === "skip-retry") {
    return node; // No modification needed
  }

  // Deep clone the node to avoid mutating the original
  const effective: WorkflowNode = {
    ...node,
    executor: { ...node.executor } as typeof node.executor,
  };

  const executor = effective.executor as Record<string, unknown>;

  switch (repair.action) {
    case "patch-prompt": {
      if (!repair.patchedPrompt) break;
      const originalPrompt = String((node.executor as Record<string, unknown>).prompt ?? "");
      const maxLen = originalPrompt.length * maxPromptMultiplier;
      // Enforce prompt length limit
      if (repair.patchedPrompt.length > maxLen && maxLen > 0) {
        executor.prompt = repair.patchedPrompt.slice(0, maxLen);
      } else {
        executor.prompt = repair.patchedPrompt;
      }
      break;
    }

    case "adjust-params": {
      if (!repair.paramOverrides) break;
      const existingArgs = (executor.args ?? {}) as Record<string, unknown>;
      executor.args = { ...existingArgs, ...repair.paramOverrides };
      break;
    }

    case "adjust-timeout": {
      if (typeof repair.timeoutOverride !== "number" || repair.timeoutOverride <= 0) break;
      // Convert ms to seconds for timeoutSeconds field
      executor.timeoutSeconds = Math.ceil(repair.timeoutOverride / 1000);
      break;
    }

    default:
      // Unknown action — return original node
      return node;
  }

  return effective;
}

/** Summarize a repair for logging. */
export function summarizeRepair(repair: GuardianRepair): string {
  switch (repair.action) {
    case "patch-prompt":
      return `prompt patched (${repair.patchedPrompt?.length ?? 0} chars)`;
    case "adjust-params":
      return `params overridden: ${Object.keys(repair.paramOverrides ?? {}).join(", ")}`;
    case "adjust-timeout":
      return `timeout → ${repair.timeoutOverride ?? "?"}ms`;
    case "skip-retry":
      return `skip retry: ${repair.reasoning.slice(0, 100)}`;
    case "retry-as-is":
      return `retry as-is: ${repair.reasoning.slice(0, 100)}`;
    default:
      return repair.reasoning.slice(0, 100);
  }
}