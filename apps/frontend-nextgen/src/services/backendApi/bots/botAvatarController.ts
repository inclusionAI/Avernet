import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';
import { userScopedParams } from './botController';
export const getBotAvatar = (id: string) =>
  backendRequest<BackendApiEnvelope<{ avatar_url: string }>>(`/openapi/v1/bots/${encodeURIComponent(id)}/avatar`, {
    params: userScopedParams(),
  });
export const saveBotAvatar = (id: string, avatarUrl: string) =>
  backendRequest<BackendApiEnvelope<{ avatar_url: string }>>(`/openapi/v1/bots/${encodeURIComponent(id)}/avatar`, {
    method: 'PUT',
    params: userScopedParams(),
    data: { avatar_url: avatarUrl },
  });
