import type { RecoveryBotOptionsPage, RecoveryListPage, RecoveryStatus, RecoveryTask } from '@/domain/recovery/types';
import { backendRequest, type BackendRequestOptions } from '../httpClient';

const base = '/openapi/v1/clawinsight/monitoring';
const recoveries = `${base}/session-recoveries`;
const taskPath = (taskId: string) => `${recoveries}/${encodeURIComponent(taskId)}`;

function request<T>(url: string, options: BackendRequestOptions = {}) {
  return backendRequest<T>(url, {
    ...options,
    injectUserId: false,
    suppressErrorNotification: true,
  });
}

export const recoveryApi = {
  list: (
    params: {
      page: number;
      pageSize: number;
      botId?: string;
      env?: string;
      status?: RecoveryStatus;
      includePresentation?: 'true';
    },
    signal?: AbortSignal,
  ) => request<RecoveryListPage>(`${recoveries}/tasks`, { params, signal }),
  detail: (taskId: string, params: { includeDiagnosis?: 'true' }, signal?: AbortSignal) =>
    request<RecoveryTask>(taskPath(taskId), { params, signal }),
  bots: (
    params: { spaceId: string; scope: 'mine'; q: string; limit: number; start: 'all'; end: 'all'; cursor?: string },
    signal?: AbortSignal,
  ) => request<RecoveryBotOptionsPage>(`${base}/bot-options`, { params, signal }),
  configure: (
    spaceId: string,
    input: { botRef: string; version: number; enabled: boolean } | { all: true; enabled: boolean },
  ) =>
    request<{ updated: number; failed: string[] }>(`${base}/configurations`, {
      method: 'POST',
      data: { spaceId, ...input },
      retryOnTransient: false,
    }),
  decide: (
    taskId: string,
    input: { plan_digest: string; approved: boolean; cooperation_done: boolean; feedback: string },
  ) => request<{ ok: true }>(`${taskPath(taskId)}/decision`, { method: 'POST', data: input, retryOnTransient: false }),
  confirmOwner: (taskId: string, requestId: string, feedback: string) =>
    request<{ ok: true }>(`${taskPath(taskId)}/owner-requests/${encodeURIComponent(requestId)}/confirm`, {
      method: 'POST',
      data: { feedback },
      retryOnTransient: false,
    }),
  terminate: (taskId: string, reason: string) =>
    request<RecoveryTask>(`${taskPath(taskId)}/terminate`, {
      method: 'POST',
      data: { reason },
      retryOnTransient: false,
    }),
};
