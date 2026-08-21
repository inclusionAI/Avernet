import { buildBcsCollaborationBatch, type BcsCollaborationBatch } from "../bcs-collaboration-protocol.js";
import { buildTemplateContext } from "../runner.js";
import type { FlowState, WorkflowNode, WorkflowSpec } from "../types.js";

export type BcsApprovalBatchApi = {
  runtime: {
    agent: {
      runEmbeddedPiAgent: (params: {
        sessionKey: string;
        prompt: string;
        timeoutMs: number;
      }) => Promise<{
        output?: string;
        error?: string;
      }>;
    };
  };
};

export type BcsApprovalBatchOutcome =
  | {
      status: "waiting";
      batch: BcsCollaborationBatch;
      waitPrompt: string;
    }
  | {
      status: "failed";
      error: string;
      rawError?: unknown;
    };

export async function executeBcsApprovalBatch(params: {
  nodes: WorkflowNode[];
  workflow: WorkflowSpec;
  flowState: FlowState;
  flowId: string;
  skillRoot: string;
  api: BcsApprovalBatchApi;
}): Promise<BcsApprovalBatchOutcome> {
  const { nodes, workflow, flowState, flowId, skillRoot, api } = params;

  if (!flowState.bcsGroupId) {
    return { status: "failed", error: "bcs collaboration batch requires bcsGroupId in flowState" };
  }

  try {
    const batch = buildBcsCollaborationBatch({
      workflow,
      state: flowState,
      nodes,
      flowId,
      templateCtx: buildTemplateContext(flowState, skillRoot),
      nodeTemplateCtx: (node) => buildTemplateContext(flowState, skillRoot, {}, { currentNodeId: node.id }),
    });
    const routeParams = {
      to: batch.targets,
      reason: workflow.collaboration?.routing?.defaultReason ?? "workflow 协作批量分发",
    };
    const prompt = [
      "请发送一条 workflow 协作批量请求。",
      "先输出且只输出 collaboration_batch_request JSON。",
      "不要摘要、不要自然语言说明、不要 Markdown 包裹。",
      "然后只调用一次 bcs_route 工具，不能调用多次。",
      "bcs_route 参数必须严格使用给出的 to[]/reason。",
      "",
      "bcs_route 参数 JSON：",
      JSON.stringify(routeParams, null, 2),
      "",
      "完整 collaboration_batch_request JSON：",
      JSON.stringify(batch.request, null, 2),
    ].join("\n");

    const run = await api.runtime.agent.runEmbeddedPiAgent({
      sessionKey: `group:${flowState.bcsGroupId.substring(0, 8)}`,
      prompt,
      timeoutMs: 30_000,
    });

    if (run.error) {
      return { status: "failed", error: `bcs collaboration batch embedded-agent error: ${run.error}` };
    }

    return {
      status: "waiting",
      batch,
      waitPrompt: `已发起 ${batch.tasks.length} 个 BCS 协作任务，等待 ${batch.tasks.length} 个回复`,
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { status: "failed", error: `bcs collaboration batch failed: ${message}`, rawError: err };
  }
}
