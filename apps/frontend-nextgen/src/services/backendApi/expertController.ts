import type { ExpertDetail, ExpertPage, ExpertQuery, ExpertBotAccepted, ExpertBotCreationStatus } from '@/domain/expertMarket';
import { backendRequest } from './httpClient';
import { userScopedParams } from './bots/botController';
import type { AvernetBotCreateRequest } from '@/domain/botWorkshop';
import type { BackendApiEnvelope } from './types';

export const EXPERT_ENDPOINT = '/openapi/v1/experts';

export function requestExpertPage(query: ExpertQuery, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<ExpertPage>>(EXPERT_ENDPOINT, {
    method: 'GET', params: { ...query, category: query.category || undefined }, injectUserId: false, signal,
  });
}

export function requestExpertCategories(signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<string[]>>(`${EXPERT_ENDPOINT}/categories`, {
    method: 'GET', params: undefined, injectUserId: false, signal,
  });
}

export function requestExpertDetail(expertId: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<ExpertDetail>>(`${EXPERT_ENDPOINT}/${encodeURIComponent(expertId)}`, {
    method: 'GET', params: undefined, injectUserId: false, signal,
  });
}


/** Uses the same caller resolution as ordinary Bot creation on non-Workspace pages. */
export function requestExpertBotCreation(expertId: string, body: AvernetBotCreateRequest) {
  return backendRequest<BackendApiEnvelope<ExpertBotAccepted>>(`${EXPERT_ENDPOINT}/${encodeURIComponent(expertId)}/bots`, {
    method: 'POST', params: userScopedParams(), data: body,
  });
}

export function requestExpertBotCreationStatus(botId: string) {
  return backendRequest<BackendApiEnvelope<ExpertBotCreationStatus>>(`/openapi/v1/bots/${encodeURIComponent(botId)}/with-manifest/status`, {
    method: 'GET', params: userScopedParams(),
  });
}
