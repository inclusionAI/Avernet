import type {
  FacadeBinding,
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
  listFacadeBindings,
  listFlowRuns,
  listWorkflows,
  listWorkflowTypes,
  saveWorkflow,
} from '@/services/backendApi';
import { load as yamlLoad } from 'js-yaml';

export type { NodeExecution };

export interface FlowRunDetail {
  run: FlowRun;
  nodes: NodeExecution[];
}

export type WorkflowImportErrorField = 'yaml' | 'command';

export class WorkflowImportValidationError extends Error {
  constructor(public readonly field: WorkflowImportErrorField, message: string) {
    super(message);
    this.name = 'WorkflowImportValidationError';
  }
}

export interface CreateWorkflowFromYamlInput {
  yaml: string;
  command?: string;
  remark?: string;
  botOwnerId?: string;
  botId?: string;
}

const COMMAND_PATTERN = /^[a-z0-9][a-z0-9_-]*[a-z0-9]$/;

function parseWorkflowYaml(yaml: string, workflows: WorkflowListItem[]): WorkflowSpec {
  const trimmedYaml = yaml.trim();
  if (!trimmedYaml) {
    throw new WorkflowImportValidationError('yaml', '请粘贴 YAML 内容');
  }

  let parsed: unknown;
  try {
    parsed = yamlLoad(trimmedYaml);
  } catch (error) {
    throw new WorkflowImportValidationError('yaml', error instanceof Error ? error.message : 'YAML 语法错误');
  }

  if (
    !parsed ||
    typeof parsed !== 'object' ||
    !('id' in parsed) ||
    !parsed.id ||
    !('version' in parsed) ||
    !parsed.version ||
    !('title' in parsed) ||
    !parsed.title ||
    !('nodes' in parsed) ||
    !Array.isArray(parsed.nodes)
  ) {
    throw new WorkflowImportValidationError('yaml', '无效：YAML 必须包含 id、version、title 和 nodes 数组');
  }

  const spec = parsed as WorkflowSpec;
  if (workflows.some((workflow) => workflow.workflowId === spec.id)) {
    throw new WorkflowImportValidationError('yaml', `工作流 ID "${spec.id}" 已存在，请修改 id 后重试`);
  }
  return spec;
}

function resolveFacade(
  spec: WorkflowSpec,
  commandInput: string | undefined,
  remarkInput: string | undefined,
  bindings: FacadeBinding[],
): { command: string; remark?: string } {
  const command = commandInput?.trim() || spec.id;
  if (!COMMAND_PATTERN.test(command)) {
    throw new WorkflowImportValidationError(
      'command',
      '命令必须为 kebab-case 或 snake-case（小写字母、数字、连字符、下划线）',
    );
  }
  const existing = bindings.find((binding) => binding.command === command && binding.workflowId !== spec.id);
  if (existing) {
    throw new WorkflowImportValidationError('command', `命令 "/${command}" 已绑定到工作流 "${existing.workflowId}"`);
  }
  const remark = remarkInput?.trim();
  return { command, ...(remark ? { remark } : {}) };
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

  async createWorkflowFromYaml(
    input: CreateWorkflowFromYamlInput,
    workflows: WorkflowListItem[],
  ): Promise<WorkflowSpec> {
    const spec = parseWorkflowYaml(input.yaml, workflows);
    const bindings = assertArray<FacadeBinding>(await listFacadeBindings(), 'listFacadeBindings');
    const facade = resolveFacade(spec, input.command, input.remark, bindings);
    const specWithFacade: WorkflowSpec = { ...spec, facade };
    return saveWorkflow({
      workflowId: spec.id,
      spec: specWithFacade,
      ...(input.botOwnerId ? { botOwnerId: input.botOwnerId } : {}),
      ...(input.botId ? { botId: input.botId } : {}),
      facade,
    });
  },
};
