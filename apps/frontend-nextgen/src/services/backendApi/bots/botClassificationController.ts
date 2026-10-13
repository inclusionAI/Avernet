import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export type BotClassificationResponse = BackendApiEnvelope<{
  bot_id: string;
  bot_type: 'personal' | 'service' | 'desktop';
}>;

/** 最小分类接口仅要求登录，不要求 Bot owner/collaborator 权限。 */
export function getBotClassification(botId: string, entityId?: string) {
  return backendRequest<BotClassificationResponse>(`/openapi/v1/bots/${encodeURIComponent(botId)}/classification`, {
    method: 'GET',
    params: entityId ? { entity_id: entityId } : undefined,
    injectUserId: false,
  });
}
