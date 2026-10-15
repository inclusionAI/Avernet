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

export interface BotContainerListDto {
  bot_id: string;
  summary: { total: number; healthy: number; abnormal: number; restarting: number; unknown: number };
  instances: BotContainerDto[];
}

export function listBotContainers(botId: string) {
  return backendRequest<BackendApiEnvelope<BotContainerListDto>>(
    `/openapi/v1/bots/${encodeURIComponent(botId)}/containers`,
  );
}

export function restartBotContainer(botId: string, instanceId: string) {
  return backendRequest<BackendApiEnvelope<{ accepted: boolean }>>(
    `/openapi/v1/bots/${encodeURIComponent(botId)}/containers/${encodeURIComponent(instanceId)}/restart`,
    { method: 'POST' },
  );
}
