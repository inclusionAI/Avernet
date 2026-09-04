import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export interface BotRegistrationTokenDto {
  token: string;
  expires_at: number;
  note?: string;
}

export function getBotRegistrationToken(signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BotRegistrationTokenDto>>('/openapi/v1/collaboration/register/token', {
    method: 'GET',
    injectUserId: false,
    signal,
  });
}
