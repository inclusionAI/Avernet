/**
 * Failure notification Markdown formatter for DingTalk notifications.
 *
 * Produces Markdown messages with:
 * - Workflow and node context
 * - Error summary (truncated to 200 chars)
 * - Clickable sidebar link to the failed flow run
 *
 * Two formatters:
 * - buildFailureNotificationMarkdown — single-node failure
 * - buildAggregatedFailureMarkdown — multi-node aggregated failure
 */
import { buildDingTalkSidebarLink, buildFlowRunUrl } from "./dingtalk-sidebar.js";

export type FailureNotificationInput = {
  /** Workflow title from WorkflowSpec.title */
  workflowTitle: string;
  /** Workflow ID from WorkflowSpec.id */
  workflowId: string;
  /** Flow execution ID */
  flowId: string;
  /** Failed node ID */
  nodeId: string;
  /** Failed node title (human-readable) */
  nodeTitle?: string;
  /** Error message */
  error: string;
  /** Retry attempt number */
  attempt: number;
  /** ClawWeb base URL for constructing run links */
  clawwebBaseUrl: string;
  /** Whether to include run link. Default: true */
  includeRunLink?: boolean;
};

export type AggregatedFailureNotificationInput = {
  workflowTitle: string;
  workflowId: string;
  flowId: string;
  /** All failed nodes in this run */
  failedNodes: Array<{
    nodeId: string;
    nodeTitle?: string;
    error: string;
    attempt: number;
  }>;
  clawwebBaseUrl: string;
  includeRunLink?: boolean;
  /** Maximum number of failed nodes to show. Default: 5 */
  maxNodesShown?: number;
};

/**
 * Build a single-node failure notification message.
 *
 * Output example:
 * ```
 * ### ❌ 工作流失败通知
 *
 * **工作流**: 风险审核流程 (risk-review-pipeline)
 *
 * **失败节点**: check-risk
 *
 * **执行 ID**: 2fe4322c-7100-46e3-a9eb-9d74fe4317a9
 *
 * **重试次数**: 3
 *
 * **错误信息**: Agent execution timeout after 300s
 *
 * ---
 *
 * [📋 查看工作流详情](dingtalk://dingtalkclient/page/link?url=...&pc_slide=true)
 * ```
 */
export function buildFailureNotificationMarkdown(
  input: FailureNotificationInput,
): string {
  const {
    workflowTitle, workflowId, flowId,
    nodeId, nodeTitle, error, attempt,
    clawwebBaseUrl, includeRunLink = true,
  } = input;

  const lines: string[] = [
    `### ❌ 工作流失败通知`,
    ``,
    `**工作流**: ${workflowTitle} (${workflowId})`,
    ``,
    `**失败节点**: ${nodeTitle ?? nodeId}`,
    ``,
    `**执行 ID**: ${flowId}`,
    ``,
    `**重试次数**: ${attempt}`,
    ``,
    `**错误信息**: ${truncateError(error)}`,
  ];

  if (includeRunLink) {
    const runUrl = buildFlowRunUrl(flowId, clawwebBaseUrl);
    const sidebarLink = buildDingTalkSidebarLink("📋 查看工作流详情", runUrl);
    lines.push("", "---", "", sidebarLink);
  }

  return lines.join("\n");
}

/**
 * Build an aggregated multi-node failure notification message.
 *
 * Shows up to maxNodesShown failed nodes, with a "…还有 N 个节点失败"
 * truncation marker for the rest.
 *
 * Output example (3 failed nodes):
 * ```
 * ### ❌ 工作流失败通知
 *
 * **工作流**: 风险审核流程 (risk-review-pipeline)
 *
 * **执行 ID**: 2fe4322c-7100-46e3-a9eb-9d74fe4317a9
 *
 * **失败节点数**: 3
 *
 * ---
 *
 * ❌ **风险检查** (重试 3 次)
 *    - 错误: Agent execution timeout after 300s
 *
 * ❌ **数据获取** (重试 2 次)
 *    - 错误: BaaS service returned HTTP 500
 *
 * ❌ **报告生成** (重试 1 次)
 *    - 错误: Template rendering failed
 *
 * ---
 *
 * [📋 查看工作流详情](dingtalk://dingtalkclient/page/link?url=...&pc_slide=true)
 * ```
 */
export function buildAggregatedFailureMarkdown(
  input: AggregatedFailureNotificationInput,
): string {
  const {
    workflowTitle, workflowId, flowId,
    failedNodes, clawwebBaseUrl,
    includeRunLink = true, maxNodesShown = 5,
  } = input;

  const lines: string[] = [
    `### ❌ 工作流失败通知`,
    ``,
    `**工作流**: ${workflowTitle} (${workflowId})`,
    ``,
    `**执行 ID**: ${flowId}`,
    ``,
    `**失败节点数**: ${failedNodes.length}`,
    "",
    "---",
  ];

  const nodesToShow = failedNodes.slice(0, maxNodesShown);
  for (const node of nodesToShow) {
    lines.push("", `❌ **${node.nodeTitle ?? node.nodeId}** (重试 ${node.attempt} 次)`);
    lines.push(`   - 错误: ${truncateError(node.error)}`);
  }

  if (failedNodes.length > maxNodesShown) {
    const remaining = failedNodes.length - maxNodesShown;
    lines.push("", `...还有 ${remaining} 个节点失败`);
  }

  if (includeRunLink) {
    const runUrl = buildFlowRunUrl(flowId, clawwebBaseUrl);
    const sidebarLink = buildDingTalkSidebarLink("📋 查看工作流详情", runUrl);
    lines.push("", "---", "", sidebarLink);
  }

  return lines.join("\n");
}

/** Truncate error message to 200 characters for readability in chat messages. */
function truncateError(error: string): string {
  return error.length > 200 ? error.substring(0, 200) + "..." : error;
}