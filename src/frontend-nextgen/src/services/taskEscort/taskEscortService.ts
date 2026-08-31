import type {
  FlowRun,
  FlowRunsParams,
  NodeExecution,
  SaveWorkflowRequest,
  WorkflowListItem,
  WorkflowSpec,
  WorkflowTypeRow,
} from '@/services/backendApi';
import {
  getFlowRun,
  getWorkflow,
  listFlowRuns,
  listWorkflows,
  listWorkflowTypes,
  saveWorkflow,
} from '@/services/backendApi';

export type { NodeExecution };

export interface FlowRunDetail {
  run: FlowRun;
  nodes: NodeExecution[];
}

function assertArray<T>(data: unknown, label: string): T[] {
  if (!Array.isArray(data)) {
    throw new Error(`后端返回了非预期的数据类型: ${label} 期望数组，实际为 ${typeof data}`);
  }
  return data as T[];
}

export const taskEscortService = {
  async listWorkflowTypes(botOwnerId?: string, botId?: string): Promise<WorkflowTypeRow[]> {
    const data = await listWorkflowTypes(botOwnerId, botId);
    return assertArray<WorkflowTypeRow>(data, 'listWorkflowTypes');
  },

  async listFlowRuns(params: FlowRunsParams): Promise<FlowRun[]> {
    const res = await listFlowRuns(params);
    if (!res || !Array.isArray(res.runs)) {
      throw new Error('后端返回了非预期的数据类型: listFlowRuns 期望 { runs: [...] } 结构');
    }
    return res.runs;
  },

  async listWorkflows(botOwnerId?: string, botId?: string): Promise<WorkflowListItem[]> {
    const data = await listWorkflows(botOwnerId, botId);
    return assertArray<WorkflowListItem>(data, 'listWorkflows');
  },

  async getWorkflow(workflowId: string): Promise<WorkflowSpec> {
    return getWorkflow(workflowId);
  },
  async getFlowRun(flowId: string): Promise<FlowRunDetail> {
    const detail = await getFlowRun(flowId);
    return { run: detail.run, nodes: detail.nodes ?? [] };
  },

  async saveWorkflow(body: SaveWorkflowRequest): Promise<WorkflowSpec> {
    return saveWorkflow(body);
  },
};
