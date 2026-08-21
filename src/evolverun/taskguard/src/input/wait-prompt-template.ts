export type WaitPromptChoice = {
  label: string;
  command: string;
};

export type WaitPromptParams = {
  workflowName: string;
  nodeTitle: string;
  choices?: WaitPromptChoice[];
  rejectCommand?: string;
};

/**
 * Render a structured waitPrompt (R4) that gives the AI Agent
 * clear, machine-parseable instructions about how to handle user
 * natural language when a workflow is waiting for a decision.
 *
 * This replaces the plain-text waitPrompt with a Markdown-formatted
 * action hint that explicitly tells the Agent to call
 * `workflow_engine_dispatch` when the user expresses a choice.
 */
export function renderStructuredWaitPrompt(params: WaitPromptParams): string {
  const parts: string[] = [];

  parts.push(`## ⚡ 工作流等待你的决策`);
  parts.push("");
  parts.push(`当前流程「${params.workflowName}」在节点「${params.nodeTitle}」等待你的选择。`);

  if (params.choices && params.choices.length > 0) {
    parts.push("");
    parts.push("### 可选操作");
    parts.push("| 操作 | 命令 |");
    parts.push("|------|------|");
    for (const choice of params.choices) {
      parts.push(`| ${choice.label} | ${choice.command} |`);
    }
    if (params.rejectCommand) {
      parts.push(`| 拒绝 | ${params.rejectCommand} |`);
    }
  }

  parts.push("");
  parts.push("### ⚠️ 重要：你的回复应包含明确的动作选择");
  parts.push("当用户表达了选择意图（包括自然语言），请优先使用 `workflow_engine_dispatch` 工具执行对应命令。");
  parts.push("例如用户说\"走快速\"，你应调用 workflow_engine_dispatch(command: \"confirm choice: fast\")。");

  return parts.join("\n");
}