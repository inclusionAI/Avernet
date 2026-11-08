import type { HostingConfig, HostingConversations, HostingSettings, HostingStatus } from '@/domain/dwsHosting';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

const path = (id: string) => `/openapi/v1/bots/${encodeURIComponent(id)}/dws-hosting`;
const options = { injectUserId: true, retryOnTransient: false };
export const dwsHostingController = {
  eligibility: (id: string, signal?: AbortSignal) =>
    backendRequest<BackendApiEnvelope<{ available: boolean }>>(`${path(id)}/eligibility`, { ...options, signal }),
  status: (id: string, signal?: AbortSignal) =>
    backendRequest<BackendApiEnvelope<HostingStatus>>(path(id), { ...options, signal }),
  settings: (id: string, signal?: AbortSignal) =>
    backendRequest<BackendApiEnvelope<HostingConfig>>(`${path(id)}/settings`, { ...options, signal }),
  save: (id: string, version: number, settings: HostingSettings) =>
    backendRequest<BackendApiEnvelope<HostingConfig>>(`${path(id)}/settings`, {
      ...options,
      method: 'PUT',
      data: { expected_version: version, settings },
    }),
  enable: (id: string, version: number, enabled: boolean) =>
    backendRequest<BackendApiEnvelope<{ enabled: boolean }>>(`${path(id)}/enabled`, {
      ...options,
      method: 'PUT',
      data: { expected_version: version, enabled },
    }),
  authorize: (id: string) =>
    backendRequest<BackendApiEnvelope<{ authorization: string }>>(`${path(id)}/authorization`, {
      ...options,
      method: 'POST',
    }),
  check: (id: string) =>
    backendRequest<BackendApiEnvelope<{ authenticated: boolean }>>(`${path(id)}/authorization/check`, {
      ...options,
      method: 'POST',
    }),
  conversations: (id: string, offset: number) =>
    backendRequest<BackendApiEnvelope<HostingConversations>>(`${path(id)}/conversations`, {
      ...options,
      params: { offset, limit: 20 },
    }),
  takeover: (id: string, conversationId: string, takenOver: boolean) =>
    backendRequest<BackendApiEnvelope<{ taken_over: boolean }>>(`${path(id)}/takeover`, {
      ...options,
      method: 'PUT',
      data: { conversation_id: conversationId, taken_over: takenOver },
    }),
};
