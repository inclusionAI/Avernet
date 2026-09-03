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
  user_visibility?: 'public' | 'protected' | 'private';
  agent_code?: string;
  created_at?: number;
  created_by?: string;
  env?: string;
  /** Bot 类型原始枚举值，与通用 /openapi/v1/bots 目录接口保持一致。 */
  bot_type?: string;
  provider?: { name: string; provider_id: string };
  /** 由通用 /openapi/v1/bots 目录接口补充的引擎标识。 */
  engine?: string;
  updated_at?: number;
  descriptor?: CollaborationBotDescriptorDto;
  /** 由已发布的 Bot 属性接口/目录透传；缺失时不得用默认值伪造真实策略。 */
  friend_ext?: Record<string, unknown>;
  friend_check_in_strategy?: 'OPEN' | 'APPROVAL' | 'DEPT_FREE';
  /** 任务认领开关：开启后该 Bot 可被任务派发消费；由 mine 接口回填。 */
  task_claim_mode?: boolean;
  /** 任务发现(Dream)开关：任务发现模块专用，与任务执行阶段无关。 */
  task_dream_mode?: boolean;
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
  descriptor?: Partial<CollaborationBotDescriptorDto>;
  /** 好友相关扩展对象为整体替换语义，调用方必须先合并当前值。 */
  friend_ext?: Record<string, unknown>;
  friend_check_in_strategy?: 'OPEN' | 'APPROVAL' | 'DEPT_FREE';
  /** 更新任务认领开关（true=授权并参与任务派发消费）。 */
  task_claim_mode?: boolean;
  /** 更新任务发现(Dream)开关。 */
  task_dream_mode?: boolean;
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

// 更新当前用户管理的 Bot 协作字段；好友策略字段来自已部署 Avernet owner-scoped PATCH DTO。
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

// 批量查询协作 Bot 详情；bots/query 直接使用完整复合 Bot ID。
export function queryCollaborationBots(body: QueryCollaborationBotsRequest | QueryCollaborationBotsBody) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<CollaborationBotDto>>>(COLLABORATION_BOT_ENDPOINTS.query, {
    method: 'POST',
    data: body,
    injectUserId: false,
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
export function listBotCandidates(bot_id: string, params: ListBotCandidatesParams = {}, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<CollaborationCandidateDto>>>(
    COLLABORATION_BOT_ENDPOINTS.candidates(bot_id),
    { method: 'GET', params: params as Record<string, unknown>, injectUserId: false, signal },
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
