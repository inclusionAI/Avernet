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
  template_name?: string | null;
  cluster_name: string;
  bot_type: string;
  status: string;
  template_type?: string | null;
  template_config?: Record<string, unknown> | null;
  bot_template_config?: Record<string, unknown> | null;
  engine_properties?: Record<string, unknown> | null;
  space?: { space_id: string; name: string; kind: string } | null;
  collaboration: { id: number; role: 'admin' | 'member'; joined_at: string };
}

export function listCollaboratingBots(
  params: { user_id: string; page: number; page_size: number; stage?: 'draft' | 'verify' | 'online' },
  signal?: AbortSignal,
) {
  return backendRequest<BackendApiEnvelope<{ items: CollaboratingBotDto[]; total: number }>>(
    '/openapi/v1/bots/collaborations',
    { method: 'GET', params, injectUserId: false, signal },
  );
}
