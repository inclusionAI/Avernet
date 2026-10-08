import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';
const path = (botId: string, skillId: string) =>
  `/openapi/v1/bots/${encodeURIComponent(botId)}/skills/${encodeURIComponent(skillId)}/parameters`;
export const skillParameterController = {
  get: (botId: string, skillId: string, ownerId?: string) =>
    backendRequest<BackendApiEnvelope<{ parameters: Record<string, unknown> }>>(path(botId, skillId), {
      params: { owner_id: ownerId },
    }),
  save: (botId: string, skillId: string, parameters: Record<string, unknown>, ownerId?: string) =>
    backendRequest<BackendApiEnvelope<{ parameters: Record<string, unknown> }>>(path(botId, skillId), {
      method: 'PUT',
      params: { owner_id: ownerId },
      data: { parameters },
    }),
};
