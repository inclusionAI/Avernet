/**
 * 工作流菜单 API Controller。
 *
 * 收口共享工作流列表与详情；可执行菜单与普通浏览分别调用，不依赖 HarnessFlow 运行记录模块。
 * 后端接口由 clawweb 提供：GET /api/workflows、GET /api/workflows/{workflowId}。
 * 前端统一使用同源相对路径，由开发/部署代理转发到 clawweb。
 */
import { backendRequest } from '../httpClient';

export interface WorkflowListItem {
  workflowId: string;
  title: string;
  packId: string | null;
  updatedAt: number;
}

export interface WorkflowSpec {
  id: string;
  version: string;
  title: string;
  nodes: Array<Record<string, unknown>>;
  facade?: { command?: string; remark?: string };
  [key: string]: unknown;
}

const BASE = '/api/workflows';

type WorkflowListPermission = 'view' | 'execute';

async function requestWorkflows(botOwnerId: string | undefined, botId: string | undefined, permission: WorkflowListPermission): Promise<WorkflowListItem[]> {
  // ownerBotId 形如 "default:146836"（botId:ownerUserId）。后端把 botId 与 botOwnerId 拼接成完整标识查询，
  // 故 botId 必须取冒号前的裸值；botOwnerId 单独透传 ownerUserId。若 botId 仍带 :146836 后缀会拼重复。
  const bareBotId = botId?.trim().split(':')[0] || undefined;
  const params: Record<string, string> = {};
  if (botOwnerId) params.botOwnerId = botOwnerId;
  if (bareBotId) params.botId = bareBotId;
  if (permission === 'execute') params.permission = permission;
  const data = await backendRequest<WorkflowListItem[]>(BASE, {
    method: 'GET',
    params: Object.keys(params).length > 0 ? params : undefined,
  });
  return Array.isArray(data) ? data : [];
}

export async function listWorkflows(botOwnerId?: string, botId?: string): Promise<WorkflowListItem[]> {
  return requestWorkflows(botOwnerId, botId, 'view');
}

/**
 * 查询当前认证用户可执行的工作流，保留通用列表 API 的可选资源范围。
 * 任务入口由 taskService 校验完整 Bot/owner 范围；botOwnerId 不代替请求认证。
 * 依赖服务端 execute 权限过滤上线（PR4275），不回退到 view。
 */
export async function listExecutableWorkflows(botOwnerId?: string, botId?: string): Promise<WorkflowListItem[]> {
  return requestWorkflows(botOwnerId, botId, 'execute');
}

export function getWorkflowDetail(workflowId: string): Promise<WorkflowSpec> {
  return backendRequest<WorkflowSpec>(`${BASE}/${encodeURIComponent(workflowId)}`, {
    method: 'GET',
  });
}
