/**
 * 工作流菜单 API Controller。
 *
 * 仅服务单聊「+」菜单里的工作流列表与详情，不依赖 HarnessFlow 运行记录模块。
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

export async function listWorkflows(botOwnerId?: string, botId?: string): Promise<WorkflowListItem[]> {
  // ownerBotId 形如 "default:146836"（botId:ownerUserId）。后端把 botId 与 botOwnerId 拼接成完整标识查询，
  // 故 botId 必须取冒号前的裸值；botOwnerId 单独透传 ownerUserId。若 botId 仍带 :146836 后缀会拼重复。
  const bareBotId = botId?.trim().split(':')[0] || undefined;
  const params: Record<string, string> = {};
  if (botOwnerId) params.botOwnerId = botOwnerId;
  if (bareBotId) params.botId = bareBotId;
  const data = await backendRequest<WorkflowListItem[]>(BASE, {
    method: 'GET',
    params: Object.keys(params).length > 0 ? params : undefined,
  });
  return Array.isArray(data) ? data : [];
}

export function getWorkflowDetail(workflowId: string): Promise<WorkflowSpec> {
  return backendRequest<WorkflowSpec>(`${BASE}/${encodeURIComponent(workflowId)}`, {
    method: 'GET',
  });
}
