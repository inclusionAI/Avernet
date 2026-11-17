import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export type BotContainerStatus = 'healthy' | 'restarting' | 'abnormal' | 'unknown';

export interface BotContainerDto {
  id: string;
  node?: string | null;
  cpu?: string | null;
  memory?: string | null;
  status: BotContainerStatus;
  internal_status?: string | null;
  engine: string;
  provider?: string | null;
  provider_instance_id?: string | null;
  created_at?: string | null;
}

export interface BotContainerScaleApprovalDto {
  status: string;
  approval_url?: string | null;
  current_count?: number | null;
  delta?: number | null;
  target_count?: number | null;
}

export interface BotContainerListDto {
  bot_id: string;
  summary: { total: number; healthy: number; abnormal: number; restarting: number; unknown: number };
  instances: BotContainerDto[];
  scale_approval?: BotContainerScaleApprovalDto | null;
}

export interface BotContainerScaleDto {
  bot_id: string;
  accepted: boolean;
  current_count: number;
  delta: number;
  target_count: number;
  approval: BotContainerScaleApprovalDto;
}

/** ownerId: Bot 拥有者工号,分享/协作场景按拥有者圈定 bot 查找范围(缺省仅按 user_id 查找);user_id 为登录人工号,显式传递(自动注入依赖 identityStore,身份未就绪时静默缺参,不可依赖)。 */
export function listBotContainers(botId: string, userId: string, ownerId?: string) {
  return backendRequest<BackendApiEnvelope<BotContainerListDto>>(
    `/openapi/v1/bots/${encodeURIComponent(botId)}/containers`,
    ownerId ? { params: { user_id: userId, owner_id: ownerId } } : { params: { user_id: userId } },
  );
}

export function restartBotContainer(botId: string, instanceId: string, userId: string) {
  return backendRequest<BackendApiEnvelope<{ accepted: boolean }>>(
    `/openapi/v1/bots/${encodeURIComponent(botId)}/containers/${encodeURIComponent(instanceId)}/restart`,
    { method: 'POST', params: { user_id: userId } },
  );
}

/** Request a signed instance-count change; execution is gated by approval. */
export function scaleBotContainers(botId: string, delta: number, userId: string, ownerId?: string) {
  return backendRequest<BackendApiEnvelope<BotContainerScaleDto>>(
    `/openapi/v1/bots/${encodeURIComponent(botId)}/containers/scale`,
    {
      method: 'POST',
      params: ownerId ? { user_id: userId, owner_id: ownerId } : { user_id: userId },
      data: { delta },
    },
  );
}
