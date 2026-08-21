import type {
  WorkflowNode,
  ExecutorResult,
  FlowState,
  BcsRouteExecutor,
  BcsRouteSelector,
  CollaborationExecutor,
  WorkflowSpec,
} from "../types.js";
import type { TemplateContext } from "../runner.js";
import { resolveTemplate } from "../runner.js";
import { getLegacyApprovalExecutor } from "../legacy-runtime.js";

// BCS 路由实现：通过 runEmbeddedPiAgent 让模型在 BCS group session 中调用 bcs_route tool。
//
// 执行链路：
// 1. runEmbeddedPiAgent 在 BCS group session 中运行
// 2. 模型输出消息文本 + 调用 bcs_route tool
// 3. bcs_route handler 写入 pendingRouteByRunId
// 4. handleChatSend 收尾时消费 intent → chat.event 带 routing → BCS 分发
//
// 关键约束：
// - runEmbeddedPiAgent 必须跑在 BCS group session 中，bcs_route tool 才会被注册
// - pendingRouteByRunId 是 run 级生命周期，只在 handleChatSend 一次 run 内有效
//
// 源码参考：
// - bcs_route tool 注册：ocb/src/plugin/packages/openclaw-channel-bcn/src/index.ts 24-69
// - handleBcsRouteTool：ocb/src/plugin/packages/openclaw-channel-bcn/src/inbound-handler.ts 1107
// - pendingRouteByRunId：inbound-handler.ts 53
// - intent 消费（附到 chat.event.routing）：inbound-handler.ts 311-338

export type BcsRouteApi = {
  runtime: {
    agent: {
      runEmbeddedPiAgent: (params: Record<string, unknown>) => Promise<{
        output?: string;
        error?: string;
      }>;
    };
  };
};

function routeTargetLabel(target: BcsRouteSelector): string {
  return "value" in target && typeof target.value === "string" ? `${target.type}:${target.value}` : target.type;
}

function routeTargetsLabel(targets: BcsRouteSelector[]): string {
  return targets.map(routeTargetLabel).join(", ");
}

function resolveApprovalRouteTargets(exec: NonNullable<ReturnType<typeof getLegacyApprovalExecutor>>): BcsRouteSelector[] {
  if (exec.route?.to?.length) return exec.route.to;
  return [];
}

function resolveCollaborationRouteTargets(exec: CollaborationExecutor): BcsRouteSelector[] {
  if (exec.route?.to?.length) return exec.route.to;
  return [];
}

export async function executeBcsRoute(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  api: unknown,
  flowState: FlowState,
  options: { workflow?: WorkflowSpec } = {},
): Promise<ExecutorResult> {
  const approvalExecutor = getLegacyApprovalExecutor(node);
  if (node.executor.type !== "bcs-route" && node.executor.type !== "collaboration" && !approvalExecutor) {
    return { status: "failed", error: "not a bcs-route/collaboration/approval node" };
  }

  let targets: BcsRouteSelector[];
  let reason: string;
  let message: string;

  if (approvalExecutor) {
    const exec = approvalExecutor;
    targets = resolveApprovalRouteTargets(exec);
    if (targets.length === 0) {
      return {
        status: "failed",
        error: `approval executor for node ${node.id} requires route.to`,
      };
    }
    reason = exec.route?.reason ?? options.workflow?.collaboration?.routing?.defaultReason ?? node.title;
    message = resolveTemplate(exec.message, templateCtx);
  } else if (node.executor.type === "collaboration") {
    const exec = node.executor as CollaborationExecutor;
    targets = resolveCollaborationRouteTargets(exec);
    if (targets.length === 0) {
      return {
        status: "failed",
        error: `collaboration executor for node ${node.id} requires route.to`,
      };
    }
    reason = exec.route?.reason ?? options.workflow?.collaboration?.routing?.defaultReason ?? node.title;
    message = resolveTemplate(exec.message, templateCtx);
  } else {
    const exec = node.executor as BcsRouteExecutor;
    const resolvedTarget = resolveTemplate(exec.target, templateCtx);
    targets = [{ type: "name", value: resolvedTarget || exec.target }];
    reason = options.workflow?.collaboration?.routing?.defaultReason ?? node.title;
    message = resolveTemplate(exec.message, templateCtx);
  }

  if (!flowState.bcsGroupId) {
    // No BCS session context — return routing info as result without attempting dispatch.
    // This allows local/CLI testing to complete the workflow without a BCS group session,
    // while production sessions with bcsGroupId continue through the normal BCS routing path.
    const targetLabel = routeTargetsLabel(targets);
    return {
      status: "succeeded",
      result: {
        routed: false,
        reason: "no bcsGroupId — routing recorded but not dispatched",
        targets: targets.map(t => ({ type: t.type, value: "value" in t ? t.value : undefined })),
        targetLabel,
        message,
      },
    };
  }

  const bcsRouteApi = api as BcsRouteApi;
  const bcsSessionKey = `group:${flowState.bcsGroupId.substring(0, 8)}`;

  try {
    const routeParams = { to: targets, reason };
    const targetLabel = routeTargetsLabel(targets);
    const prompt = [
      `你需要通过 bcs_route 工具把以下 workflow 协作消息路由给指定目标。`,
      ``,
      `请先输出消息内容，然后调用 bcs_route 工具。`,
      ``,
      `bcs_route 调用参数：`,
      JSON.stringify(routeParams, null, 2),
      ``,
      `消息内容：`,
      message,
    ].join("\n");

    const run = await bcsRouteApi.runtime.agent.runEmbeddedPiAgent({
      sessionKey: bcsSessionKey,
      prompt,
      timeoutMs: 30_000,
    });

    if (run.error) {
      return { status: "failed", error: `bcs-route embedded-agent error: ${run.error}` };
    }

    // bcs_route 是异步的：审批 bot 后续回复通过 before_agent_reply hook 拦截回写
    return {
      status: "waiting",
      waitConfig: {
        prompt: `等待 ${targetLabel} 回复`,
        hint: `已通过 BCS 路由给 ${targetLabel}`,
        waitKind: "bcs-approval",
      },
    };
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    return { status: "failed", error: `bcs-route execution failed: ${errMsg}`, rawError: err };
  }
}
