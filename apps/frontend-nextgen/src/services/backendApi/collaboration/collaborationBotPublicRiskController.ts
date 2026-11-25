// Bot 公开风险前置检测 Controller（OpenAPI 协作面，bot-public-risk-check）。
// 契约对齐 Avernet #3224：/openapi/v1/collaboration/bots/{bot_id}/public/risk-checks*
// - {bot_id} 为复合地址（协作域 Bot UUID），与 collaborationPublicationController 同惯例不做额外编码；
// - user_id query 必填（真实认证调用人，浏览器不做应用身份）；
// - envelope {code, message, data, request_id}：创建 HTTP 202 与幂等重放 HTTP 200 的 code 均在
//   2xx 成功段（202000/200000），统一经 isEnvelopeSuccess 判定，不按 HTTP status 分叉；
// - phase / security_status 为开放字符串，未知值由上层按保守锁定兜底（riskFlowState）。

import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';
import type { ViewDept } from './collaborationPublicationController';

export const COLLABORATION_BOT_PUBLIC_RISK_ENDPOINTS = {
  /** POST /openapi/v1/collaboration/bots/{bot_id}/public/risk-checks（创建，202；幂等重放 200） */
  create: (botId: string) => `/openapi/v1/collaboration/bots/${botId}/public/risk-checks`,
  /** GET .../current（查询当前有效申请；无申请时 data:null） */
  current: (botId: string) => `/openapi/v1/collaboration/bots/${botId}/public/risk-checks/current`,
  /** GET .../{risk_check_id}（查询指定记录，不可达返回 404 抛 BackendRequestError） */
  detail: (botId: string, riskCheckId: string) =>
    `/openapi/v1/collaboration/bots/${botId}/public/risk-checks/${riskCheckId}`,
  /** POST .../{risk_check_id}/refresh（立即查询原安全单，不创建新单） */
  refresh: (botId: string, riskCheckId: string) =>
    `/openapi/v1/collaboration/bots/${botId}/public/risk-checks/${riskCheckId}/refresh`,
} as const;

/** 公开目标（记录视图）。 */
export interface BotPublicRiskTarget {
  public_scope: string;
  visibility: string;
  view_depts: ViewDept[];
  permission_owner?: string | null;
  friend_approval?: string | null;
}

/** 风险申请记录（创建/查询的 data，refresh 的 record）。phase/security_status 为开放字符串。 */
export interface BotPublicRiskCheckRecord {
  risk_check_id: string;
  phase: string;
  /** 'DETECTING' | 'REJECT' | 'PASS'；null 尚无结论；其他值以保守锁定兜底 */
  security_status: string | null;
  /** 外部安全检测工单链接；仅非空时透出 */
  detail_url: string | null;
  auto_poll_paused: boolean;
  auto_poll_until: string | null;
  next_action_at: string | null;
  target: BotPublicRiskTarget;
  error_reason: string | null;
  invalidation_reason: string | null;
  /** 已关联审批实例 ID；非审批结果 */
  approval_puid: string | null;
}

/** 创建请求体。client_request_id：同一用户操作（含网络重试）保持相同，新意图新 ID。 */
export interface CreateBotPublicRiskRequest {
  client_request_id: string;
  public_scope: 'user' | 'agent';
  /** public=全部可见；protected=限定组织可申请（view_depts 必须非空）。本接口不接受撤回（private）。 */
  visibility: 'public' | 'protected';
  /** protected 必须非空；public 传空数组 */
  view_depts: ViewDept[];
}

/** refresh 返回 data。outcome='NOT_QUERIED' 仅作状态展示，不构成任何检测结论。 */
export interface BotPublicRiskRefreshResult {
  outcome: string;
  record: BotPublicRiskCheckRecord;
}

/** 创建风险申请（新建 202 / 幂等重放 200，两者 code 都在 2xx 段）。 */
export function createBotPublicRiskCheck(
  botId: string,
  userId: string,
  body: CreateBotPublicRiskRequest,
  signal?: AbortSignal,
) {
  return backendRequest<BackendApiEnvelope<BotPublicRiskCheckRecord>>(
    COLLABORATION_BOT_PUBLIC_RISK_ENDPOINTS.create(botId),
    {
      method: 'POST',
      params: { user_id: userId },
      data: body,
      injectUserId: false,
      signal,
    },
  );
}

/** 查询当前有效风险申请；public_scope 必填（'user' | 'agent'），无申请时 data:null。 */
export function getCurrentBotPublicRiskCheck(
  botId: string,
  userId: string,
  params: { public_scope: 'user' | 'agent' },
  signal?: AbortSignal,
) {
  return backendRequest<BackendApiEnvelope<BotPublicRiskCheckRecord | null>>(
    COLLABORATION_BOT_PUBLIC_RISK_ENDPOINTS.current(botId),
    {
      method: 'GET',
      params: { user_id: userId, ...params },
      injectUserId: false,
      signal,
    },
  );
}

/** 查询指定风险申请记录。 */
export function getBotPublicRiskCheck(botId: string, userId: string, riskCheckId: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BotPublicRiskCheckRecord>>(
    COLLABORATION_BOT_PUBLIC_RISK_ENDPOINTS.detail(botId, riskCheckId),
    {
      method: 'GET',
      params: { user_id: userId },
      injectUserId: false,
      signal,
    },
  );
}

/** 立即查询原安全单（不创建新安全单）。 */
export function refreshBotPublicRiskCheck(botId: string, userId: string, riskCheckId: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BotPublicRiskRefreshResult>>(
    COLLABORATION_BOT_PUBLIC_RISK_ENDPOINTS.refresh(botId, riskCheckId),
    {
      method: 'POST',
      params: { user_id: userId },
      injectUserId: false,
      signal,
    },
  );
}
