/**
 * 任务执行 Loop Controller —— 单一边界，统一 Envelope{code,message,data,request_id}。
 * - execute：端点前缀由 capability getTaskApiBase 注入（Open Core /openapi/v1/collaboration/tasks，
 *   internal overlay /api/v1/collaboration/tasks）。
 * - dashboard/list：统一读公开面 TASK_READ_API_BASE=/openapi/v1/collaboration/tasks。
 *
 * 默认走相对路径（dev proxy / prod 网关转发）。
 * 测试场景可通过 baseUrl 直连本地 singlebox（localhost:8888），绕过 proxy。
 */
import { getCapabilities } from '@/capabilities';
import { TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import type { Envelope, ExecuteTaskRequest, ExecuteTaskResponse, TaskListItem } from '@/services/tasks/taskModel';
import { backendRequest } from '../httpClient';
import type { BackendApiPage } from '../types';
const DEFAULT_BASE = ''; // 相对路径 → 走 dev 代理/gateway 统一出口

/** 解析 task API 路径前缀；capability 缺省回退内面 /api/v1（向后兼容）。请求期调用，确保 capability 已装填。 */
function taskApiBase(): string {
  return getCapabilities().getTaskApiBase().value ?? '/api/v1/collaboration/tasks';
}

export async function executeTask(
  req: ExecuteTaskRequest,
  baseUrl: string = DEFAULT_BASE,
): Promise<Envelope<ExecuteTaskResponse>> {
  const path = `${taskApiBase()}/execute`;
  const url = baseUrl ? `${baseUrl.replace(/\/+$/, '')}${path}` : path;
  return backendRequest<Envelope<ExecuteTaskResponse>>(url, {
    method: 'POST',
    data: req,
  });
}

export async function dashboardTask(
  taskId: string,
  includeActionLog = false,
  baseUrl: string = DEFAULT_BASE,
): Promise<Envelope<unknown>> {
  const path = `${TASK_READ_API_BASE}/dashboard`;
  const url = baseUrl ? `${baseUrl.replace(/\/+$/, '')}${path}` : path;
  return backendRequest<Envelope<unknown>>(url, {
    method: 'GET',
    params: { task_id: taskId, include_action_log: includeActionLog },
  });
}

/**
 * GET task list 查询参数。
 * - user_id：必填，按用户过滤；
 * - status：可选，精确匹配（后端 Status 枚举值，运行时态）；
 * - page / page_size：可选，服务端分页（1-based，page_size ≤ 100）。
 *   两者必须同时传或同时缺省：同时传 → data 为 Page{total,items}；都不传 → data 为全量数组（兼容历史契约）。
 *   注：status 过滤值是后端运行时态(PENDING/PLANNING/RUNNING/DONE/FAILED/HUNG/CANCELLED)，
 *   与列表返回的产品态(DEFINED/EXECUTING/REVIEWING…)词汇表不同，故前端状态 Tab 过滤仍在客户端做。
 */
export interface ListTasksParams {
  user_id: string;
  status?: string;
  page?: number;
  page_size?: number;
}

export async function listTasks(
  params: ListTasksParams,
  baseUrl: string = DEFAULT_BASE,
): Promise<Envelope<BackendApiPage<TaskListItem> | TaskListItem[]>> {
  const path = `${TASK_READ_API_BASE}/list`;
  const url = baseUrl ? `${baseUrl.replace(/\/+$/, '')}${path}` : path;
  return backendRequest<Envelope<BackendApiPage<TaskListItem> | TaskListItem[]>>(url, {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
}

/**
 * 下载任务级 OBJECT_FILE 产物内容。
 *
 * 端点为任务产物内容代理接口（内部部署路径为 /api/v1/collaboration/tasks/artifacts/{id}/content）。
 * 后端代理对象存储读写并发回原始字节；不携带 X-Relay-Token。
 */
export function downloadTaskArtifactContent(artifactId: string, baseUrl: string = DEFAULT_BASE): Promise<Blob> {
  const path = `${taskApiBase()}/artifacts/${encodeURIComponent(artifactId)}/content`;
  const url = baseUrl ? `${baseUrl.replace(/\/+$/, '')}${path}` : path;
  return backendRequest<Blob>(url, {
    method: 'GET',
    responseType: 'blob',
    injectUserId: false,
    operation: 'download-task-artifact-content',
  });
}
