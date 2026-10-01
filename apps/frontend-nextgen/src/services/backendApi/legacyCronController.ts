import { backendRequest } from './httpClient';
import type { BackendApiEnvelope, BackendUnknownRecord } from './types';

export interface LegacyCronTaskRequest {
  bot_id: string;
  owner_id?: string;
  name: string;
  schedule: string;
  command: string;
  model?: string;
  runtime?: string;
  timezone?: string;
  timeout_secs?: number;
  notify?: { enabled: boolean; user_ids: string[] };
}

export function listTasks(params: { bot_id: string; owner_id?: string }) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord | BackendUnknownRecord[]>>('/api/cron', {
    method: 'GET',
    params,
    operation: 'list-dima-auto-cron',
    target: 'legacy-agentclaw',
  });
}

export function createTask(body: LegacyCronTaskRequest) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>('/api/cron', {
    method: 'POST',
    data: body,
    operation: 'create-dima-auto-cron',
    target: 'legacy-agentclaw',
  });
}
