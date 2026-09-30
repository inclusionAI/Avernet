import type { WorkflowNode, ExecutorResult } from "../types.js";
import type { TemplateContext } from "../runner.js";
import { resolveTemplate } from "../runner.js";

export async function executeHumanWait(
  node: WorkflowNode,
  templateCtx: TemplateContext,
): Promise<ExecutorResult> {
  if (node.executor.type !== "human") {
    return { status: "failed", error: "not a human node" };
  }

  const resolvedPrompt = resolveTemplate(node.executor.prompt, templateCtx);

  return {
    status: "waiting",
    waitConfig: {
      prompt: resolvedPrompt,
      hint: resolvedPrompt,
      waitKind: node.executor.waitKind ?? "human-confirm",
    },
  };
}
