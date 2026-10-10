import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

/** 团队 Bot 摘要；entity_id 为实体，owner_id 为拥有者用户，不可混用。 */
export interface CollaboratingBotDto {
  bot_id: string;
  bot_name: string;
  bot_desc: string;
  entity_id: string;
  owner_id: string;
  engine: string;
  cluster_name: string;
  bot_type: string;
  status: string;
  collaboration: { id: number; role: 'admin' | 'member'; joined_at: string };
}

export function listCollaboratingBots(
  params: { user_id: string; page: number; page_size: number },
  signal?: AbortSignal,
) {
  return backendRequest<BackendApiEnvelope<{ items: CollaboratingBotDto[]; total: number }>>(
    '/openapi/v1/bots/collaborations',
    { method: 'GET', params, injectUserId: false, signal },
  );
}
