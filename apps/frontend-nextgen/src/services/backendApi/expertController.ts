import type { ExpertDetail, ExpertPage, ExpertQuery } from '@/domain/expertMarket';
import { backendRequest } from './httpClient';
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
