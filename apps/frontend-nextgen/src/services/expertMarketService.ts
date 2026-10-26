import type { ExpertDetail, ExpertPage, ExpertQuery } from '@/domain/expertMarket';
import {
  EXPERT_ENDPOINT,
  requestExpertCategories,
  requestExpertDetail,
  requestExpertPage,
} from './backendApi/expertController';
import { backendRequest, BackendRequestError } from './backendApi/httpClient';
import { type BackendApiEnvelope, isEnvelopeSuccess } from './backendApi/types';

function catalogData<T>(response: BackendApiEnvelope<T>, path: string): T {
  if (!isEnvelopeSuccess(response) || response.data === undefined || response.data === null) {
    throw new BackendRequestError(response.message || '专家目录响应无效', { apiPath: path, data: response });
  }
  return response.data;
}

/** 发现页与模板选择器共用服务端语义；可见范围始终由认证上下文决定。 */
export async function listExperts(query: ExpertQuery, signal?: AbortSignal): Promise<ExpertPage> {
  const page = catalogData(
    await requestExpertPage({ ...query, keyword: query.keyword.trim() }, signal),
    EXPERT_ENDPOINT,
  );
  if (typeof page.total !== 'number' || !Array.isArray(page.items)) {
    throw new BackendRequestError('专家目录分页响应无效', { apiPath: EXPERT_ENDPOINT, data: page });
  }
  return page;
}

export async function listExpertCategories(signal?: AbortSignal): Promise<string[]> {
  return catalogData(await requestExpertCategories(signal), `${EXPERT_ENDPOINT}/categories`);
}

export async function getExpert(expertId: string, signal?: AbortSignal): Promise<ExpertDetail> {
  return catalogData(await requestExpertDetail(expertId, signal), `${EXPERT_ENDPOINT}/${encodeURIComponent(expertId)}`);
}

export interface ExpertBotAccepted {
  bot_id: string;
  iframe_url: string;
  redirect_url: string;
}
export interface ExpertBotCreationStatus extends ExpertBotAccepted {
  state:
    | 'AWAITING_AUTHORIZATION'
    | 'CREATING'
    | 'APPLYING'
    | 'READY'
    | 'CREATE_FAILED'
    | 'APPLY_FAILED'
    | 'AUTHORIZATION_REJECTED'
    | 'AUTHORIZATION_EXPIRED';
  message?: string;
  bot?: Record<string, unknown> | null;
  apply?: Record<string, unknown> | null;
}

export async function createExpertBot(
  expertId: string,
  body: import('@/domain/botWorkshop').AvernetBotCreateRequest,
): Promise<ExpertBotAccepted> {
  const path = `${EXPERT_ENDPOINT}/${encodeURIComponent(expertId)}/bots`;
  return catalogData(
    await backendRequest<BackendApiEnvelope<ExpertBotAccepted>>(path, {
      method: 'POST',
      data: body,
      injectUserId: true,
    }),
    path,
  );
}
export async function getExpertBotCreationStatus(botId: string): Promise<ExpertBotCreationStatus> {
  const path = `/openapi/v1/bots/${encodeURIComponent(botId)}/with-manifest/status`;
  return catalogData(
    await backendRequest<BackendApiEnvelope<ExpertBotCreationStatus>>(path, { method: 'GET', injectUserId: true }),
    path,
  );
}
