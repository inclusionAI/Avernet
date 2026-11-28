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
  request<{ candidates: RsiCandidate[] }>(scope, `${base(scope)}/runs/${encodeURIComponent(runId)}/candidates`);

export interface RsiCandidate {
  candidate_id: string;
  revision: string;
  verdict: string;
}
export interface RsiSplitSummary {
  split: string;
  mean_delta: number | null;
  ci_low: number | null;
  ci_high: number | null;
  newly_failing: number;
  detail_visible: boolean;
}
export interface RsiGateCheck {
  name: string;
  status: string;
  detail: string;
}
export interface RsiGate {
  id: string | null;
  outcome: string;
  risk: { tier: string; raised_by: string[] };
  verdict: string;
  checks: RsiGateCheck[];
  decided_at: string;
}
export interface RsiReview {
  status: string;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  promotion_id: string | null;
}
export interface RsiPatchItem {
  op_index: number;
  op: string;
  target: string;
  tier: string;
  reason: string;
}
export interface RsiCandidateReport {
  candidate_id: string;
  revision_id: string;
  revision_seq: number | null;
  parent_revision_id: string;
  parent_revision_seq: number | null;
  run_id: string;
  strategy: string;
  strategy_version: string;
  patch_summary: RsiPatchItem[];
  diff_path: string;
  rationale: string;
  evidence: unknown[];
  verdict: string;
  splits: RsiSplitSummary[];
  cost: { parent_usd_per_case?: number | null; candidate_usd_per_case?: number | null };
  flags: string[];
  gate: RsiGate | null;
  review: RsiReview | null;
  manual_promotion?: RsiManualPromotion | null;
}
export interface RsiReviewItem extends RsiReview {
  candidate_id: string;
  revision_id: string;
  parent_revision_id: string;
  run_id: string;
  strategy: string;
  strategy_version: string;
  risk_tier: string;
  gate_decision_id: string;
  rationale: string;
  evidence: unknown[];
}
export interface RsiReviewDecision {
  review: RsiReview;
  promotion?: unknown;
  promotion_note?: string;
}
export const getRsiCandidateReport = (scope: RsiScope, candidateId: string) =>
  request<RsiCandidateReport>(scope, `${base(scope)}/candidates/${encodeURIComponent(candidateId)}`);
export const getRsiReviewQueue = (scope: RsiScope, status = 'open') =>
  request<{ total: number; items: RsiReviewItem[] }>(scope, `${base(scope)}/review-queue`, {
    params: { status },
  });
export const approveRsiCandidate = (scope: RsiScope, candidateId: string, key: string, reason: string) =>
  request<RsiReviewDecision>(scope, `${base(scope)}/candidates/${encodeURIComponent(candidateId)}:approve`, {
    method: 'POST',
    headers: { 'Idempotency-Key': key },
    data: { reason },
  });
export const rejectRsiCandidate = (scope: RsiScope, candidateId: string, key: string, reason: string) =>
  request<RsiReviewDecision>(scope, `${base(scope)}/candidates/${encodeURIComponent(candidateId)}:reject`, {
    method: 'POST',
    headers: { 'Idempotency-Key': key },
    data: { reason },
  });

export interface GenomeBaseline {
  revision: string;
  created: boolean;
}
/** 基因组侧的显式冷启动建基线：只记录当前配置，不 apply；需要 ADMIN。 */
export const initializeGenomeBaseline = (scope: RsiScope) =>
  request<GenomeBaseline>(scope, `/openapi/v1/bots/${encodeURIComponent(scope.botId)}/genome/baseline:initialize`, {
    method: 'POST',
  });

/** Display-only, redacted final response; not prompts or model hidden reasoning. */
export interface RsiStrategyOutput {
  schema_version: 1;
  attempt: number;
  outcome: 'not_called' | 'no_changes' | 'changes_proposed' | 'invalid_proposal' | 'cancelled';
  reason: string;
  rationale: string;
  edits: { path: string; content: string; changed: boolean }[];
  raw_output: string;
  raw_output_truncated: boolean;
}
export const getRsiStrategyOutput = (scope: RsiScope, runId: string) =>
  request<{ output: RsiStrategyOutput | null }>(
    scope,
    `${base(scope)}/runs/${encodeURIComponent(runId)}/strategy-output`,
  );

/** Genome operations keep the same authenticated Bot scope as RSI; promotion is not RSI approval. */
export interface GenomeRef {
  name: string;
  revision: string;
  labels: string[];
}
export interface GenomeDrift {
  active: string;
  observed: string;
}
export interface GenomeDiff {
  revision: string;
  against: string;
  changes: { gene: string; kind: 'added' | 'removed' | 'modified'; diff: string | null }[];
  risk_tier: string;
  rewrite_flags: string[];
}
export interface GenomeCapture {
  id: string;
  status: 'draft' | 'candidate' | 'accepted' | 'rejected' | 'promoted' | 'archived';
  created: boolean;
}
export interface GenomePromotion {
  id: string;
  revision: string;
  from_revision: string | null;
  status: 'pending' | 'applying' | 'applied' | 'applied_partial' | 'apply_failed';
  apply_id: string | null;
  apply_detail: string;
  reason: string;
  created_at: string;
}
export const isGenomePromotionTerminal = (status: GenomePromotion['status']) =>
  ['applied', 'applied_partial', 'apply_failed'].includes(status);
const genomeBase = (scope: RsiScope) => `/openapi/v1/bots/${encodeURIComponent(scope.botId)}/genome`;
export const getGenomeRefs = (scope: RsiScope) => request<{ refs: GenomeRef[] }>(scope, `${genomeBase(scope)}/refs`);
export const getGenomeDrift = (scope: RsiScope) =>
  request<{ drift: GenomeDrift | null }>(scope, `${genomeBase(scope)}/drift`);
/** Capture records content only. It does not move active or make a candidate promotable. */
export const captureGenomeRevision = (scope: RsiScope) =>
  request<GenomeCapture>(scope, `${genomeBase(scope)}/revisions:capture`, { method: 'POST' });
export const getGenomeDiff = (scope: RsiScope, revision: string, against: string) =>
  request<GenomeDiff>(scope, `${genomeBase(scope)}/revisions/${encodeURIComponent(revision)}/diff`, {
    params: { against },
  });
export const promoteGenomeRevision = (
  scope: RsiScope,
  revision: string,
  expectedRevision: string,
  reason: string,
) =>
  request<GenomePromotion>(scope, `${genomeBase(scope)}/promotions`, {
    method: 'POST',
    data: { revision, expected_revision: expectedRevision, reason },
  });
export const getGenomePromotion = (scope: RsiScope, id: string) =>
  request<GenomePromotion>(scope, `${genomeBase(scope)}/promotions/${encodeURIComponent(id)}`);
export const listGenomePromotions = (scope: RsiScope) =>
  request<{ items: GenomePromotion[]; total: number }>(scope, `${genomeBase(scope)}/promotions`, {
    params: { page_no: 1, page_size: 20 },
  });

/** Human authorization is separate from the original evaluation verdict and from delivery. */
export interface RsiHumanAuthorization {
  id: string;
  basis: 'human';
  candidate_id: string;
  run_id: string;
  revision_id: string;
  parent_revision_id: string;
  strategy: string;
  actor: string;
  user_id: string;
  reason: string;
  authorized_at: string;
  checks_at_authorization: { verdict: string; floor: string; floor_problems: string[] };
}
export interface RsiManualPromotion {
  authorization: RsiHumanAuthorization | null;
  promotion?: GenomePromotion | null;
}
export interface RsiAuthorizationRequest {
  revision_id: string;
  parent_revision_id: string;
  reason: string;
}
export const authorizeRsiPromotion = (
  scope: RsiScope, candidateId: string, key: string, body: RsiAuthorizationRequest,
) => request<RsiManualPromotion>(scope, `${base(scope)}/candidates/${encodeURIComponent(candidateId)}:authorize-promotion`, {
  method: 'POST', headers: { 'Idempotency-Key': key }, data: body,
});
export const applyRsiCandidate = (scope: RsiScope, candidateId: string, authorizationId: string) =>
  request<RsiManualPromotion>(scope, `${base(scope)}/candidates/${encodeURIComponent(candidateId)}:apply`, {
    method: 'POST', data: { authorization_id: authorizationId },
  });
