import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';
export const getBotAvatar = (id: string) =>
  backendRequest<BackendApiEnvelope<{ avatar_url: string }>>(`/openapi/v1/bots/${encodeURIComponent(id)}/avatar`);
export const saveBotAvatar = (id: string, avatarUrl: string) =>
  backendRequest<BackendApiEnvelope<{ avatar_url: string }>>(`/openapi/v1/bots/${encodeURIComponent(id)}/avatar`, {
    method: 'PUT',
    data: { avatar_url: avatarUrl },
  });
