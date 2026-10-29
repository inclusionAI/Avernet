import type { ExpertDetail, ExpertPage, ExpertQuery, ExpertBotAccepted, ExpertBotCreationStatus } from '@/domain/expertMarket';
import {
  EXPERT_ENDPOINT,
  requestExpertCategories,
  requestExpertDetail,
  requestExpertPage,
  requestExpertBotCreation,
  requestExpertBotCreationStatus,
  requestExpertBotRecovery,
  requestExpertBotApplyReport,
  requestExpertBotLastApply,
} from './backendApi/expertController';
import { BackendRequestError } from './backendApi/httpClient';
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

export type { ExpertBotAccepted, ExpertBotCreationStatus } from '@/domain/expertMarket';

export async function createExpertBot(
  expertId: string,
  body: import('@/domain/botWorkshop').AvernetBotCreateRequest,
): Promise<ExpertBotAccepted> {
  const path = `${EXPERT_ENDPOINT}/${encodeURIComponent(expertId)}/bots`;
  return catalogData(
    await requestExpertBotCreation(expertId, body),
    path,
  );
}
export async function getExpertBotCreationStatus(botId: string): Promise<ExpertBotCreationStatus> {
  const path = `/openapi/v1/bots/${encodeURIComponent(botId)}/with-manifest/status`;
  const creation = catalogData(await requestExpertBotCreationStatus(botId), path);
  if (!['READY', 'APPLY_FAILED'].includes(creation.state)) return creation;
  const latest = catalogData(await requestExpertBotLastApply(botId), 'config-manifest/last-apply');
  // The creation endpoint deliberately describes its original task. Subsequent
  // Apply reports are the fact source for current configuration, including a
  // recovery started before this page was reopened.
  if (!latest.apply_id || !['explicit', 'put'].includes(latest.trigger || '')) return creation;
  return {
    ...creation, apply: latest, message: undefined,
    recoverable: latest.state?.kind === 'manifest' && Boolean(latest.state.reference),
    state: latest.result === 'RUNNING' ? 'APPLYING' : latest.result === 'SUCCEEDED' ? 'READY' : 'APPLY_FAILED',
  };
}


/** Service owns transport error interpretation; presentation consumes only the outcome category. */
export function classifyExpertSubmissionFailure(failure: unknown): 'rejected' | 'unknown' {
  return failure instanceof BackendRequestError && failure.status !== undefined && failure.status >= 400 && failure.status < 500
    ? 'rejected' : 'unknown';
}

export async function recoverExpertBot(botId: string) {
  const accepted = catalogData(await requestExpertBotRecovery(botId), 'config-manifest/apply');
  if (!accepted.apply_id) throw new Error('配置恢复响应缺少 Apply 编号，请先在 Bot 管理中确认结果。');
  return accepted;
}
export async function getExpertBotApplyReport(botId: string, applyId: string) {
  return catalogData(await requestExpertBotApplyReport(botId, applyId), 'config-manifest/applies');
}

export async function getExpertBotLastApply(botId: string) {
  return catalogData(await requestExpertBotLastApply(botId), 'config-manifest/last-apply');
}
