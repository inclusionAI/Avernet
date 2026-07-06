/**
 * ActorController - Bot Actor 管理 API
 * 替换原有的 discoverBots 接口
 */

import { request } from '@umijs/max';

// ── 类型定义 ──────────────────────────────────────────────────────────────

/** Actor 能力信息 */
export interface ActorCapabilities {
  description: string;
  domains: string[];
  name: string;
  scopes: string[];
  skills: { name: string }[];
}

/** 动态状态 */
export interface DynamicStatus {
  status: 'active' | 'offline';
  last_active_at?: number;
}

/** Actor Bot 信息 */
export interface ActorBot {
  bot_uuid: string;
  capabilities: ActorCapabilities;
  is_friend: boolean;
  status: 'online' | 'offline';
  visibility: 'public' | 'protected' | 'private';
  dynamic_status?: DynamicStatus;
  // search 接口特有的字段
  score?: number;
  short_profile?: string;
}

/** Actor 列表请求参数 */
export interface ActorListParams {
  current_bot_uuid: string;
  cooperatable_only?: boolean;
  page_no?: number;
  page_size?: number;
  name?: string;
}

/** Actor 列表响应 */
export interface ActorListResponse {
  bots: ActorBot[];
  total: number;
}

/** Actor 搜索请求参数 */
export interface ActorSearchParams {
  current_bot_uuid: string;
  cooperatable_only?: boolean;
  q: string;
}

/** Actor 搜索响应中的推荐上下文 */
export interface ActorSearchRecommendResponse {
  trace_id?: string;
  response?: string;
}

/** Actor 搜索响应中的上下文 */
export interface ActorSearchContext {
  recommend_response: ActorSearchRecommendResponse | null;
}

/** Actor 搜索响应 */
export interface ActorSearchResponse {
  bots: ActorBot[];
  context: ActorSearchContext;
  trace_id?: string;
}

// ── API 函数 ──────────────────────────────────────────────────────────────

/**
 * 获取 Actor 列表
 * 用于：添加好友、拉起协作-公开bot搜索
 *
 * @param params 查询参数
 */
export async function getActorList(
  params: ActorListParams,
): Promise<ActorListResponse> {
  const res = await request<ActorListResponse>('/bcnproxy/actors/list', {
    method: 'GET',
    params: {
      current_bot_uuid: params.current_bot_uuid,
      cooperatable_only: params.cooperatable_only ?? false,
      page_no: params.page_no ?? 1,
      page_size: params.page_size ?? 20,
      ...(params.name ? { name: params.name } : {}),
    },
  });
  return res;
}

/**
 * 搜索 Actor（智能搜索）
 * 用于：拉起协作-智能搜索
 *
 * @param params 查询参数
 */
export async function searchActors(
  params: ActorSearchParams,
): Promise<ActorSearchResponse> {
  const res = await request<ActorSearchResponse>('/bcnproxy/actors/search', {
    method: 'GET',
    params: {
      current_bot_uuid: params.current_bot_uuid,
      cooperatable_only: params.cooperatable_only ?? true,
      q: params.q,
    },
  });
  return res;
}
