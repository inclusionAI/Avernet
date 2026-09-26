import type { TemplateContext } from "./runner.js";
import type { WorkflowNode } from "./types.js";

/**
 * 取节点 executor 级 skipWhen(任意 executor 类型,不限定 approval)。
 * 用户常把 skipWhen/skipResult 写在 executor 块内(与 prompt/timeoutSeconds 对齐),
 * 引擎需对所有 executor 类型读 executor.skipWhen,否则 embedded-agent 等节点
 * 配了 executor 级 skipWhen 也读不到、跳过失效。
 */
function readExecutorSkipWhen(node: WorkflowNode): Record<string, unknown> | undefined {
  const executor = (node as { executor?: { skipWhen?: Record<string, unknown> } }).executor;
  if (executor?.skipWhen && Object.keys(executor.skipWhen).length > 0) return executor.skipWhen;
  return undefined;
}

/**
 * 取节点声明的 skipWhen:node 级优先,回退到 executor 级(任意 executor 类型,向后兼容 approval)。
 */
export function readNodeSkipWhen(node: WorkflowNode): Record<string, unknown> | undefined {
  if (node.skipWhen && Object.keys(node.skipWhen).length > 0) return node.skipWhen;
  // executor 级 skipWhen 对所有节点类型生效 —— 原仅 approval 经 getLegacyApprovalExecutor
  // 回退,embedded-agent 等把 skipWhen 写在 executor 块内时被忽略,跳过闸读不到。
  // readExecutorSkipWhen 读任意 executor.skipWhen,已覆盖原 approval 兼容路径。
  return readExecutorSkipWhen(node);
}

/**
 * 求值 skipWhen 条件:多条件 AND;点路径取值;boolean/number 的 string 归一。
 * 与原 evaluateSkipWhen 逻辑一致,只是解除了"仅 approval"耦合。
 */
export function evaluateSkipWhenConditions(
  skipWhen: Record<string, unknown>,
  templateCtx: TemplateContext,
): boolean {
  for (const [path, expected] of Object.entries(skipWhen)) {
    const parts = path.replace(/\[(\d+)\]/g, ".$1").split(".").filter(Boolean);
    let current: unknown = templateCtx;
    for (const part of parts) {
      if (current == null || typeof current !== "object") { current = undefined; break; }
      current = (current as Record<string, unknown>)[part];
    }
    let actual: unknown = current;
    if (typeof expected === "boolean" && typeof actual === "string") {
      actual = actual === "true";
    } else if (typeof expected === "number" && typeof actual === "string") {
      actual = Number(actual);
      if (Number.isNaN(actual)) actual = undefined;
    }
    if (actual !== expected) return false;
  }
  return true;
}

/**
 * 命中 skipWhen 时构造 result:approval 沿用 approved 语义(保 saveAs 兼容);
 * 其他节点用 skipResult(node 级优先,回退 executor 级),缺省 {skipped:true, reason}。
 *
 * skipResult 常与 skipWhen 一起写在 executor 块内(如 embedded-agent 节点把
 * {skipped:true, authorize_recommendation:null} 放在 executor.skipResult),
 * 故此处同样回退读 executor.skipResult,否则下游 {{nodeOutput.x.字段}} 取不到值。
 */
export function buildSkipResult(node: WorkflowNode, isApproval: boolean): Record<string, unknown> {
  if (isApproval) {
    return { approved: true, skipped: true, reason: "skipWhen matched" };
  }
  if (node.skipResult && Object.keys(node.skipResult).length > 0) return node.skipResult;
  const executorResult = (node as { executor?: { skipResult?: Record<string, unknown> } }).executor?.skipResult;
  if (executorResult && Object.keys(executorResult).length > 0) return executorResult;
  return { skipped: true, reason: "skipWhen matched" };
}