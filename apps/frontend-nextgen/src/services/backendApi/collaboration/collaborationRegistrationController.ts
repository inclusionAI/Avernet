import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export interface BotRegistrationTokenDto {
  token: string;
  expires_at: number;
  note?: string;
  registration?: {
    token_version: number;
    provider_id: string;
    allowed_modes: string[];
  };
}

export function getBotRegistrationToken(signal?: AbortSignal, providerId?: string) {
  return backendRequest<BackendApiEnvelope<BotRegistrationTokenDto>>('/openapi/v1/collaboration/register/token', {
    method: 'GET',
    injectUserId: false,
    signal,
    ...(providerId ? { params: { provider_id: providerId } } : {}),
  });
}

export interface RegistrationProviderDto {
  provider_id: string;
  slug: string;
  enabled: boolean;
}

export function getRegistrationProvider(slug: string, signal?: AbortSignal) {
  return backendRequest<RegistrationProviderDto>(
    `/api/v1/collaboration/providers/by-slug/${encodeURIComponent(slug)}`,
    {
      method: 'GET',
      injectUserId: false,
      signal,
    },
  );
}
