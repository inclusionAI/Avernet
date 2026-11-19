/** Bot RSI API through the shared authenticated Backend request client. */
import { backendRequest, type BackendRequestOptions } from '@/services/backendApi/httpClient';
import type { BackendApiEnvelope } from '@/services/backendApi/types';
import { useIdentityStore } from '@/stores/identityStore';
import type { RsiScope } from '@/types/rsi';

export interface RsiBinding {
  id: string;
  strategy: string;
  trigger: Record<string, unknown>;
  parent: string;
  allowed_genes: string[];
  verification_profile: string;
  budget: { max_usd: number; max_wall_clock_s: number };
  params?: Record<string, unknown>;
  auto_promote_ceiling?: null;
}
export interface BindingDocument {
  bindings: RsiBinding[];
  etag: string;
}
export interface RsiRun {
  id: string;
  binding_id: string;
  strategy: string;
  status: string;
  attempt: number;
  parent_revision: string;
  candidate_count: number;
  created_at: string;
  end_reason: string | null;
  summary: unknown;
  budget_used: Record<string, unknown>;
}
export interface RsiEvidence {
  jobs: { job_id: string; attempt: number; worker_id: string | null; lease_expires_at: string | null; open: number }[];
  events: { id: number; at: string; kind: string; authority: string; sandbox_id: string | null; details: unknown }[];
  next_cursor: number;
}
export interface RsiStrategy {
  id: string;
  version: string;
  enabled: boolean;
  conformance: string;
  needs: unknown;
  params_schema: unknown;
}
export const isRsiTerminal = (status: string) =>
  ['completed', 'failed', 'cancelled', 'budget_exhausted'].includes(status);

export const RSI_IDENTITY_MISSING = '当前会话未解析出用户身份（user_id），请刷新页面或重新登录后再试。';

async function request<T>(scope: RsiScope, path: string, options: BackendRequestOptions = {}): Promise<T> {
  // `user_id` 是后端这些路由的必填参数：缺失时服务端回 422 "Invalid request"，
  // 看起来像协议错误，实际是身份没带上来。这里优先用会话已解析出的 actorId。
  const actorId = scope.actorId || useIdentityStore.getState().currentIdentityId;
  if (!actorId) throw new Error(RSI_IDENTITY_MISSING);
  const params: Record<string, unknown> = { ...options.params, entity_id: scope.ownerId, user_id: actorId };
  const result = await backendRequest<BackendApiEnvelope<T>>(path, {
    ...options,
    injectUserId: false,
    retryOnTransient: false,
    params,
  });
  const code = Number(result.code);
  if (code < 200000 || code >= 300000 || !Number.isFinite(code) || result.data === undefined) {
    throw new Error(`RSI请求未确认（${result.request_id ?? '无追踪ID'}）`);
  }
  return result.data;
}
const base = (scope: RsiScope) => `/openapi/v1/bots/${encodeURIComponent(scope.botId)}/evolution`;
export const getRsiBindings = (scope: RsiScope) => request<BindingDocument>(scope, `${base(scope)}/bindings`);
export const putRsiBindings = (scope: RsiScope, document: BindingDocument) =>
  request<BindingDocument>(scope, `${base(scope)}/bindings`, {
    method: 'PUT',
    headers: { 'If-Match': JSON.stringify(document.etag) },
    data: { bindings: document.bindings },
  });
export const listRsiStrategies = (scope: RsiScope) =>
  request<{ strategies: RsiStrategy[] }>(scope, `${base(scope)}/strategies`);
export const listRsiRuns = (scope: RsiScope) =>
  request<{ runs: RsiRun[]; total: number }>(scope, `${base(scope)}/runs`);
export const submitRsiRun = (scope: RsiScope, bindingId: string, key: string) =>
  request<{ run: string }>(scope, `${base(scope)}/runs`, {
    method: 'POST',
    headers: { 'Idempotency-Key': key },
    data: { binding_id: bindingId },
  });
export const cancelRsiRun = (scope: RsiScope, runId: string) =>
  request<{ run: RsiRun }>(scope, `${base(scope)}/runs/${encodeURIComponent(runId)}:cancel`, {
    method: 'POST',
    data: { reason: 'user requested cancellation' },
  });
export const getRsiEvidence = (scope: RsiScope, runId: string) =>
  request<RsiEvidence>(scope, `${base(scope)}/runs/${encodeURIComponent(runId)}/evidence`);
export const getRsiCandidates = (scope: RsiScope, runId: string) =>
  request<{
    candidates: { candidate_id: string; revision: string; verdict: string }[];
  }>(scope, `${base(scope)}/runs/${encodeURIComponent(runId)}/candidates`);
