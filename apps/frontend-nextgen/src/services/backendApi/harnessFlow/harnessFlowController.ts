/**
 * HarnessFlow Controller - 任务护航 API
 *
 * 所有接口统一走 TeamClaw Gateway /openapi/v1/harnessflow/api/**，
 * 网关根据 application.yaml 中 harnessflow upstream 配置转发到 clawweb 后端
 * （rewrite: /openapi/v1/harnessflow → /，故 clawweb 收到 /api/... 原始路径）。
 * 注意: clawweb 后端直接返回数据，不包装在 {success, data} 结构中。
 */
import { backendRequest } from '../httpClient';

// ======================== 类型定义 ========================

export type HarnessFlowNodeStatus =
  | 'pending'
  | 'running'
  | 'postActionsRunning'
  | 'waiting'
  | 'succeeded'
  | 'failed'
  | 'blocked'
  | 'skipped';

export interface FlowRun {
  flow_id: string;
  workflow_id: string;
  workflow_title: string | null;
  status: string;
  created_by: string | null;
  started_at: number | null;
  completed_at: number | null;
  duration_ms: number | null;
  node_count: number;
  succeeded_nodes: number;
  failed_nodes: number;
  running_nodes: number;
  total_tokens: number | null;
  error_message: string | null;
  session_id: string | null;
  trigger: string | null;
}

export interface FlowRunsResponse {
  runs: FlowRun[];
  total: number;
}

export interface WorkflowNode {
  id: string;
  title?: string;
  type?: string;
  executor?: string | Record<string, unknown>;
  phase?: string;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  retry?: { maxAttempts?: number; delayMs?: number; backoff?: string };
  postActions?: Array<{
    id?: string;
    action?: string;
    required?: boolean;
    args?: Record<string, unknown>;
    saveAs?: Record<string, string>;
  }>;
  onSuccess?: Array<{
    id?: string;
    action?: string;
    required?: boolean;
    args?: Record<string, unknown>;
    saveAs?: Record<string, string>;
  }>;
  onFailure?: Array<{
    id?: string;
    action?: string;
    required?: boolean;
    args?: Record<string, unknown>;
    saveAs?: Record<string, string>;
  }>;
  onResult?: Array<{ value: string; target: string }>;
  config?: Record<string, unknown>;
  description?: string;
  timeoutMs?: number;
  dependsOn?: string[];
  branchId?: string;
  alerting?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface WorkflowSpec {
  id: string;
  version: string;
  title: string;
  nodes: WorkflowNode[];
  config?: Record<string, unknown>;
  params?: Record<string, unknown>;
  tests?: unknown[];
  requiredParams?: string[];
  input?: {
    mode?: string;
    requiredParams?: string[];
    schema?: Record<string, unknown>;
  };
  identity?: {
    key?: string;
    label?: string;
    duplicatePolicy?: string;
  };
  outputs?: Record<string, unknown>;
  debug?: { summaryKeys?: string[] };
  defaults?: {
    progress?: string;
    user?: string;
    contextPolicy?: string;
    [key: string]: unknown;
  };
  collaboration?: Record<string, unknown>;
  workflow?: {
    preflight?: unknown[];
    onStart?: unknown[];
    onFinish?: unknown[];
  };
  messages?: {
    onCreated?: string;
    onFinished?: string;
    variants?: Array<{ condition?: string; message: string }>;
  };
  facade?: { command?: string; remark?: string };
  allowedBots?: string[];
  [key: string]: unknown;
}

export interface WorkflowListItem {
  workflowId: string;
  title: string;
  packId: string | null;
  updatedAt: number;
}

export interface FacadeBinding {
  command: string;
  workflowId: string;
  packId: string | null;
  remark: string | null;
}

export interface FlowRunsParams {
  status?: string;
  workflowId?: string;
  limit?: number;
  offset?: number;
  from?: string;
  to?: string;
  botOwnerId?: string;
  botId?: string;
}

export interface SaveWorkflowRequest {
  workflowId: string;
  spec: WorkflowSpec;
  botOwnerId?: string;
  botId?: string;
  packId?: string;
  facade?: { command?: string; remark?: string };
  originalWorkflowId?: string;
}

export interface WorkflowTypeRow {
  workflow_id: string;
  workflow_title: string | null;
  run_count: number;
  last_status: string | null;
  last_run_at: number | null;
  updated_at: number | null;
}

export interface NodeExecution {
  flow_id: string;
  node_id: string;
  node_title: string | null;
  executor_type: string;
  triggered_by: string | null;
  phase: string | null;
  branch_id: string | null;
  session_key: string | null;
  session_id: string | null;
  status: HarnessFlowNodeStatus;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  input_json: string | null;
  output_json: string | null;
  error_text: string | null;
  token_usage_json: string | null;
  system_context_json: string | null;
  progress_message: string | null;
  attempt: number;
}

export interface FlowRunDetail {
  run: FlowRun;
  nodes: NodeExecution[];
}

// ======================== API 方法 ========================

const BASE = '/openapi/v1/harnessflow/api';

export async function listWorkflows(botOwnerId?: string, botId?: string): Promise<WorkflowListItem[]> {
  const params: Record<string, string> = {};
  if (botOwnerId) params.botOwnerId = botOwnerId;
  if (botId) params.botId = botId;
  return backendRequest<WorkflowListItem[]>(`${BASE}/workflows`, {
    method: 'GET',
    params: Object.keys(params).length > 0 ? params : undefined,
  });
}

/** 获取 workflow 详情（含 facade.command，用于任务工作流触发命令）。 */
export async function getWorkflowDetail(workflowId: string): Promise<WorkflowSpec> {
  return backendRequest<WorkflowSpec>(`${BASE}/workflows/${encodeURIComponent(workflowId)}`, {
    method: 'GET',
  });
}

export async function listFacadeBindings(): Promise<FacadeBinding[]> {
  return backendRequest<FacadeBinding[]>(`${BASE}/facades`, {
    method: 'GET',
  });
}

export async function listFlowRuns(params?: FlowRunsParams): Promise<FlowRunsResponse> {
  return backendRequest<FlowRunsResponse>(`${BASE}/runs`, {
    method: 'GET',
    params: params as Record<string, unknown>,
  });
}

export async function listWorkflowTypes(
  botOwnerId?: string,
  botId?: string,
  status?: string,
): Promise<WorkflowTypeRow[]> {
  const params: Record<string, string> = {};
  if (botOwnerId) params.botOwnerId = botOwnerId;
  if (botId) params.botId = botId;
  if (status) params.status = status;
  const res = await backendRequest<{ workflows: WorkflowTypeRow[]; total: number }>(`${BASE}/runs/workflow-types`, {
    method: 'GET',
    params: Object.keys(params).length > 0 ? params : undefined,
  });
  return res.workflows ?? res;
}

export async function getWorkflow(workflowId: string): Promise<WorkflowSpec> {
  return backendRequest<WorkflowSpec>(`${BASE}/workflows/${workflowId}`, {
    method: 'GET',
  });
}
/** 获取某次运行的详情（含节点执行列表） */
export async function getFlowRun(flowId: string): Promise<FlowRunDetail> {
  return backendRequest<FlowRunDetail>(`${BASE}/runs/${flowId}`, {
    method: 'GET',
  });
}

export async function saveWorkflow(body: SaveWorkflowRequest): Promise<WorkflowSpec> {
  return backendRequest<WorkflowSpec>(`${BASE}/workflows/save`, {
    method: 'POST',
    data: body,
  });
}

export async function deleteFlowRun(flowId: string): Promise<{ deleted: boolean }> {
  return backendRequest<{ deleted: boolean }>(`${BASE}/runs/${flowId}`, {
    method: 'DELETE',
  });
}
