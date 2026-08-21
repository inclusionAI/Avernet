import type { FlowState } from "../types.js";
import { buildKeywordMappings } from "./choice-keywords.js";

/**
 * Render a structured hint for the Agent when a workflow is waiting
 * but the user's message didn't exactly match a keyword.
 *
 * The hint answers three questions for the Agent:
 * 1. Is there a workflow waiting? → Yes, what workflow, what node
 * 2. What is it waiting for? → confirm/reject/choice
 * 3. What are the options? → fast/thorough/reject etc.
 *
 * The hint is injected as a supplemental context message — it does NOT
 * replace the user's message or force the Agent to take any action.
 */
export function renderWaitingHint(
  state: FlowState,
  waitingNodeId: string,
): string {
  const nodeState = state.nodeStates[waitingNodeId];
  if (!nodeState) return "";

  const workflowId = state.workflowId;
  const choiceLine = formatChoiceLine(nodeState.waitInputSchema);

  if (choiceLine) {
    return [
      `⏳ [工作流] 「${workflowId}」在「${waitingNodeId}」等待决策。`,
      `选项：${choiceLine}`,
      `如果用户意图是审批操作，请调用 workflow_choice 工具。`,
    ].join("\n");
  }

  return [
    `⏳ [工作流] 「${workflowId}」在「${waitingNodeId}」等待确认。`,
    `如果用户意图是确认或拒绝，请调用 workflow_choice 工具（action: confirm 或 reject）。`,
  ].join("\n");
}

/**
 * Format choice options from an inputSchema into a human-readable line.
 * Returns a string like: "快速(workflow_choice action:confirm choice:fast) / 深入(workflow_choice action:confirm choice:thorough) / 拒绝(workflow_choice action:reject)"
 */
function formatChoiceLine(
  inputSchema: { type?: string; required?: string[]; properties?: Record<string, any>; fields?: Record<string, any> } | undefined,
): string {
  if (!inputSchema?.properties && !inputSchema?.fields) return "";

  const props = inputSchema.properties ?? inputSchema.fields ?? {};
  const choiceField = Object.values(props).find(
    (f: any) => f?.type === "string" && Array.isArray(f?.enum) && f.enum.length > 0,
  ) as any | undefined;

  if (!choiceField) return "";

  const mappings = buildKeywordMappings(choiceField);
  const parts = mappings.map((m) => {
    const displayKeyword = m.keywords[0] ?? m.choice;
    return `${displayKeyword}(workflow_choice action:confirm choice:${m.choice})`;
  });

  // Always append the reject option
  parts.push("拒绝(workflow_choice action:reject)");

  return parts.join(" / ");
}