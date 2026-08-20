/**
 * InterventionMessageBuilder — constructs human-readable intervention messages
 * that the OpenClaw bot can parse and execute as commands.
 */

export type InterventionAction = "retry" | "skip" | "revise" | "confirm";

export type BuildParams = {
  action: InterventionAction;
  flowId: string;
  workflowTitle?: string;
  nodeId?: string;
  nodeTitle?: string;
  reason?: string;
  operatorId: string;
  operatorName: string;
};

const ACTION_LABELS: Record<InterventionAction, string> = {
  retry: "重试节点",
  skip: "跳过节点",
  revise: "修订并重跑",
  confirm: "确认继续",
};

/**
 * Build the command that the bot will execute.
 */
function buildCommand(action: InterventionAction, nodeId?: string, reason?: string): string {
  switch (action) {
    case "retry":
      return `/workflow retry${nodeId ? ` --node ${nodeId}` : ""}${reason ? ` ${reason}` : ""}`;
    case "skip":
      return `/workflow skip${nodeId ? ` --node ${nodeId}` : ""}${reason ? ` --reason "${reason}"` : ""}`;
    case "revise":
      return `/workflow revise${nodeId ? ` --node ${nodeId}` : ""}${reason ? ` "${reason}"` : ""}`;
    case "confirm":
      return "/workflow confirm";
  }
}

/**
 * Build a human-readable intervention message with an executable command.
 */
export function buildInterventionMessage(params: BuildParams): string {
  const { action, flowId, workflowTitle, nodeId, nodeTitle, reason, operatorId, operatorName } = params;
  const actionLabel = ACTION_LABELS[action];
  const command = buildCommand(action, nodeId, reason);

  const lines: string[] = [
    `🔧 [人工干预] ${operatorName}(${operatorId}) 请求干预`,
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    `📁 流程: ${workflowTitle ?? flowId} (${flowId})`,
  ];

  if (nodeId) {
    lines.push(`🔲 节点: ${nodeTitle ?? nodeId} (${nodeId})`);
  }
  lines.push(`⚡ 操作: ${actionLabel}`);
  if (reason) {
    lines.push(`📝 原因: ${reason}`);
  }
  lines.push("━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  lines.push("执行命令:");
  lines.push(command);

  return lines.join("\n");
}