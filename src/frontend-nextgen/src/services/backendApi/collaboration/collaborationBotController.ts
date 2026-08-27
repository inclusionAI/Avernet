import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

export type CollaborationBotKind = 'bot' | 'human';
export interface CollaborationBotDescriptorDto {
  domains: string[];
  scopes: string[];
  summary?: string;
  skills: Array<{ name: string; description?: string }>;
}

export interface CollaborationBotDto {
  kind: CollaborationBotKind;
  // 该 DTO 仍被 query/candidates 等历史调用共享；mine/detail 的正式字段由 Adapter 做运行时校验。

  bot_id: string;
  name?: string;
  avatar_url?: string;
  status?: 'online' | 'hidden' | 'offline';
  reachability?: 'reachable' | 'unreachable';
  visibility?: 'public' | 'protected' | 'private';
  /** 任务认领开关（每天自动扫描任务广场并认领可执行任务）。 */
  task_claim_mode?: boolean;
  /** Dream Model 开关（每天基于用户数据挖掘潜在任务并推送）。 */
  task_dream_mode?: boolean;
  agent_code?: string;
  created_at?: number;
  created_by?: string;
  env?: string;
  provider?: { name: string; provider_id: string };
  updated_at?: number;
  descriptor?: CollaborationBotDescriptorDto;
}

export interface CollaborationFriendshipDto {
  bot_uuid: string;
  friend_bot_uuid: string;
  created_at: number;
}

export interface CollaborationCandidateDto {
  bot: CollaborationBotDto;
  /** 当前视角 Bot 是否已与该候选 Bot 建立好友关系。 */
  is_friend: boolean;
}

export interface ListBotFriendshipsParams {
  offset?: number;
  limit?: number;
}

export interface ListBotCandidatesParams {
  purpose?: 'discovery' | 'collaboration';
  name?: string;
  offset?: number;
  limit?: number;
}

export interface CollaborationBotPatchBody {
  name?: string;
  visibility?: 'public' | 'protected' | 'private';
  status?: 'online' | 'hidden';
  /** 任务认领开关。 */
  task_claim_mode?: boolean;
  /** Dream Model 开关。 */
  task_dream_mode?: boolean;
  descriptor?: Partial<CollaborationBotDescriptorDto>;
}

export interface ListMyBotsParams {
  kind?: CollaborationBotKind;
  name?: string;
  status?: 'online' | 'hidden';
  reachability?: 'reachable' | 'unreachable';
  offset?: number;
  limit?: number;
}

export interface QueryCollaborationBotsBody {
  bot_ids: string[];
}

export type QueryCollaborationBotsRequest = BackendUnknownRecord;

export const COLLABORATION_BOT_ENDPOINTS = {
  mine: '/openapi/v1/collaboration/bots/mine',
  query: '/openapi/v1/collaboration/bots/query',
  detail: (bot_id: string) => `/openapi/v1/collaboration/bots/${bot_id}`,
  candidates: (bot_id: string) => `/openapi/v1/collaboration/bots/${bot_id}/candidates`,
  friendships: (bot_id: string) => `/openapi/v1/collaboration/bots/${bot_id}/friendships`,
  friendRequests: (bot_id: string) => `/openapi/v1/collaboration/bots/${bot_id}/friend-requests`,
};

// 查询我可协作的 Bot（mine 版本，精确类型）。
export async function listMyBots(params: ListMyBotsParams = {}, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<CollaborationBotDto>>>(COLLABORATION_BOT_ENDPOINTS.mine, {
    method: 'GET',
    params: params as Record<string, unknown>,
    injectUserId: false,
    signal,
  });
}

// 更新当前用户管理的 Bot 协作字段；仅维护 Gateway Swagger 已确认的 PATCH DTO。
export function patchCollaborationBot(bot_id: string, body: CollaborationBotPatchBody, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<CollaborationBotDto>>(COLLABORATION_BOT_ENDPOINTS.detail(bot_id), {
    method: 'PATCH',
    data: body,
    injectUserId: false,
    signal,
  });
}

// 旧 mine 入口别名（residual，未在新 Service 层消费）。
export function listMyCollaborationBots(params: ListMyBotsParams = {}, signal?: AbortSignal) {
  return listMyBots(params, signal);
}

// 查询协作广场 Bot（residual，未在新 Service 层消费）。
export function queryCollaborationBots(body: QueryCollaborationBotsRequest | QueryCollaborationBotsBody) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<CollaborationBotDto>>>(COLLABORATION_BOT_ENDPOINTS.query, {
    method: 'POST',
    data: body,
  });
}

// 查询视角 Bot 的好友 Bot 列表；返回好友关系，详情需再经 queryCollaborationBots 查询。
export function listBotFriendships(bot_id: string, params: ListBotFriendshipsParams = {}, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<CollaborationFriendshipDto>>>(
    COLLABORATION_BOT_ENDPOINTS.friendships(bot_id),
    { method: 'GET', params: params as Record<string, unknown>, injectUserId: false, signal },
  );
}

// 按视角 Bot 查询可协作候选 Bot；purpose=collaboration 时包含公开 Bot 与已接受好友。
export function listBotCandidates(bot_id: string, params: ListBotCandidatesParams = {}) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<CollaborationCandidateDto>>>(
    COLLABORATION_BOT_ENDPOINTS.candidates(bot_id),
    { method: 'GET', params: params as Record<string, unknown> },
  );
}

// 发送好友申请：当前身份 bot_uuid 作为路径，目标 Bot 的 to_bot_uuid 入 body。
export function createBotFriendRequest(bot_id: string, body: { to_bot_uuid: string }, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<{ request_id: string; state: string }>>(
    COLLABORATION_BOT_ENDPOINTS.friendRequests(bot_id),
    { method: 'POST', data: body, injectUserId: false, signal },
  );
}

// 查询协作 Bot 详情（residual，未在新 Service 层消费）。
export function getCollaborationBot(bot_id: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<CollaborationBotDto>>(COLLABORATION_BOT_ENDPOINTS.detail(bot_id), {
    method: 'GET',
    injectUserId: false,
    signal,
  });
}
