/** Verified clawweb management GET contracts. Mapping and validation belong to Service. */
import { backendRequest } from '../httpClient';
export interface ManagementRecordsParams {
  kind: 'analysis' | 'optimization' | 'notification';
  period: 'all' | 'today' | '7d';
  status: 'all' | 'pending' | 'dispatching' | 'waiting' | 'retryable' | 'uncertain' | 'done' | 'blocked' | 'attention';
  limit: 20 | 8;
  cursor?: string;
}
const BASE = '/openapi/v1/harnessflow/api';
const workflow = (id: string) => `${BASE}/workflows/${encodeURIComponent(id)}`;
const automation = (id: string) => `${BASE}/task-guard/workflows/${encodeURIComponent(id)}`;
function get(url: string, userId: string, signal?: AbortSignal, params: object = {}): Promise<unknown> {
  return backendRequest(url, { method: 'GET', params: { ...params, user_id: userId }, signal, injectUserId: false });
}
export const managementController = {
  access: (id: string, userId: string, signal?: AbortSignal) => get(`${workflow(id)}/access`, userId, signal),
  history: (id: string, userId: string, signal?: AbortSignal) =>
    get(`${workflow(id)}/history`, userId, signal, { limit: 20 }),
  version: (id: string, userId: string, version: number, signal?: AbortSignal) =>
    get(`${workflow(id)}/history/${version}`, userId, signal),
  deploy: (id: string, userId: string, deployNumber: number, signal?: AbortSignal) =>
    get(`${workflow(id)}/history/by-deploy/${deployNumber}`, userId, signal),
  permissions: (id: string, userId: string, signal?: AbortSignal) =>
    get(`${workflow(id)}/bot-permissions`, userId, signal),
  facades: (userId: string, signal?: AbortSignal) => get(`${BASE}/facades`, userId, signal),
  notification: (id: string, userId: string, signal?: AbortSignal) =>
    get(`${workflow(id)}/notification-config`, userId, signal),
  callbacks: (id: string, userId: string, signal?: AbortSignal) =>
    get(`${workflow(id)}/callback-configs`, userId, signal),
  policy: (id: string, userId: string, signal?: AbortSignal) => get(`${automation(id)}/auto-analysis`, userId, signal),
  status: (id: string, userId: string, signal?: AbortSignal) =>
    get(`${automation(id)}/automation-status`, userId, signal),
  records: (id: string, userId: string, params: ManagementRecordsParams, signal?: AbortSignal) =>
    get(`${automation(id)}/automation-records`, userId, signal, params),
};
