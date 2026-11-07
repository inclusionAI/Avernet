/**
 * task_config Controller —— /collaboration/tasks/config/match 探针(spec 2026-11-02)。
 *
 * 端点前缀经 ``resolveTaskApiBase`` 解析(Open Core → ``/openapi/v1/collaboration/tasks``，
 * 内部 overlay → ``/api/v1/collaboration/tasks``)，与 ``taskController`` 同走 capability；
 * 公开面经 gateway 签名 principal 鉴权，内面经 get_current_user。
 *
 * match 只回 ``{matched: bool}``，绝不回存储的配置值本身（spec anti-goal）。
 */
import { resolveTaskApiBase } from '@/services/tasks/taskConfig';
import type { Envelope } from '@/domain/tasks/models';
import { backendRequest } from '../httpClient';

export interface TaskConfigMatchRequest {
  config_name: string;
  value: string | boolean;
}

export type TaskConfigMatchEnvelope = Envelope<{ matched: boolean }>;

/** match 探针完整路径：``{resolveTaskApiBase()}/config/match``。 */
export function taskConfigMatchPath(): string {
  return `${resolveTaskApiBase()}/config/match`;
}

export async function matchTaskConfig(
  req: TaskConfigMatchRequest,
  baseUrl: string = '',
): Promise<TaskConfigMatchEnvelope> {
  const path = taskConfigMatchPath();
  const url = baseUrl ? `${baseUrl.replace(/\/+$/, '')}${path}` : path;
  return backendRequest<TaskConfigMatchEnvelope>(url, {
    method: 'POST',
    data: req,
  });
}
