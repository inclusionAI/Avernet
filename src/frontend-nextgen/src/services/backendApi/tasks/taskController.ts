/**
 * 任务执行 Loop Controller —— 单一边界，统一 Envelope{code,message,data,request_id}。
 * - execute/dashboard：内部面 /api/v1/collaboration/tasks/*
 * - list：内部面 /api/v1/collaboration/tasks/list
 *
 * 默认走相对路径（dev proxy / prod 网关转发）。
 * 测试场景可通过 baseUrl 直连本地 singlebox（localhost:8888），绕过 proxy。
 */
import type { Envelope, ExecuteTaskRequest, ExecuteTaskResponse, TaskListItem } from '@/services/tasks/taskModel';
import { backendRequest } from '../httpClient';

const DEFAULT_BASE = '';

export async function executeTask(
  req: ExecuteTaskRequest,
  baseUrl: string = DEFAULT_BASE,
): Promise<Envelope<ExecuteTaskResponse>> {
  const path = '/api/v1/collaboration/tasks/execute';
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
  const path = '/api/v1/collaboration/tasks/dashboard';
  const url = baseUrl ? `${baseUrl.replace(/\/+$/, '')}${path}` : path;
  return backendRequest<Envelope<unknown>>(url, {
    method: 'GET',
    params: { task_id: taskId, include_action_log: includeActionLog },
  });
}

export interface ListTasksParams {
  owner_user_id: string;
  status?: string;
  page?: number;
  page_size?: number;
}

export async function listTasks(
  params: ListTasksParams,
  baseUrl: string = DEFAULT_BASE,
): Promise<Envelope<TaskListItem[]>> {
  const path = '/api/v1/collaboration/tasks/list';
  const url = baseUrl ? `${baseUrl.replace(/\/+$/, '')}${path}` : path;
  return backendRequest<Envelope<TaskListItem[]>>(url, {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
}
