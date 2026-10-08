import { backendRequest } from './httpClient';
import type { BackendApiEnvelope, BackendUnknownRecord } from './types';

export interface DimaWorkspaceResponse {
  success?: boolean;
  message?: string;
  data?: { dima_space_id?: string } | null;
}

/** 旧 AgentClaw 的 DIMA 空间初始化接口，供创建后兼容动作使用。 */
export function createDimaWorkspace(botId: string, userId?: string) {
  return backendRequest<BackendApiEnvelope<DimaWorkspaceResponse['data']>>(
    `/api/aicoding/bot/${encodeURIComponent(botId)}/dima-workspace`,
    {
      method: 'POST',
      params: userId ? { user_id: userId } : undefined,
      operation: 'create-dima-workspace',
      target: 'legacy-agentclaw',
    },
  );
}

export function isDimaResponseSuccessful(response: BackendApiEnvelope<BackendUnknownRecord> | undefined) {
  return Boolean(response) && response?.success !== false;
}
