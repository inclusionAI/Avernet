import type { ApprovalExecutor, WorkflowActor, WorkflowNode, WorkflowSpec } from "./types.js";

type WorkflowDefaultsWithLegacyActors = NonNullable<WorkflowSpec["defaults"]> & {
  actors?: Record<string, WorkflowActor>;
};

export type LegacyExecutorType = WorkflowNode["executor"]["type"] | "approval";

export function getLegacyWorkflowActors(workflow: WorkflowSpec): Record<string, WorkflowActor> | undefined {
  return (workflow.defaults as WorkflowDefaultsWithLegacyActors | undefined)?.actors;
}

export function getLegacyApprovalExecutor(node: WorkflowNode): ApprovalExecutor | undefined {
  const executor = (node as { executor?: unknown }).executor;
  if (
    executor
    && typeof executor === "object"
    && (executor as { type?: unknown }).type === "approval"
  ) {
    return executor as ApprovalExecutor;
  }
  return undefined;
}

export function getLegacyExecutorType(node: WorkflowNode): LegacyExecutorType {
  return (node as { executor: { type: LegacyExecutorType } }).executor.type;
}

/**
 * 单一真相源:executor.saveAs 是否会被运行时 applySaveAs 执行。
 * 对应 controller.ts 中所有 applySaveAs 调用点的 executor类型:
 *   human (controller.ts:4986/9312), async-callback (:8138),
 *   approval/legacyApproval 即 type==="approval" (:7931/8576/10943)。
 * onResult.rerun.saveAs / onResult.wait.saveAs 不经此函数
 * (位置在 onResult 内,normalizeOnResult 已单独合法化)。
 *
 * 任何新增 applySaveAs 桩子都必须同步更新此集合,否则 validate 会误判
 * (把生效的 saveAs 标成 dead,产生假阳)。改这里时同步改 tests/saveas-liveness.test.ts 反向证明。
 */
export function isSaveAsCapableExecutor(node: WorkflowNode): boolean {
  const t = (node as { executor?: { type?: unknown } }).executor?.type;
  return t === "human" || t === "async-callback" || t === "approval";
}
