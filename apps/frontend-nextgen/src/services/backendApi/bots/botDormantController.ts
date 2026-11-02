import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';
import { userScopedParams } from './botController';

export interface BotActivationResult {
  bot_id: string;
  status: string;
  owner_id?: string | null;
  changed?: boolean | null;
  message?: string | null;
}

export function activateDormantBot(botId: string, ownerId: string, userId: string) {
  return backendRequest<BackendApiEnvelope<BotActivationResult>>(
    `/openapi/v1/bots/${encodeURIComponent(botId)}/activate`,
    { method: 'POST', params: userScopedParams({ user_id: userId, owner_id: ownerId }) },
  );
}
