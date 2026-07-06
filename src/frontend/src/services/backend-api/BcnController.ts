/**
 * BcnController - Bot 协作网络 API 控制器
 *
 * 封装 BCN 服务相关的 HTTP API 调用
 * 所有接口统一使用 /bcnproxy 前缀（代理到 BCN 服务）
 *
 * 原 GroupChatController.ts 重命名而来，新增好友关系和可见性接口
 */

import type { CreateGroupParams, GroupStrategy } from '@/pages/GroupChat/types';
import { request } from '@umijs/max';

// === BCN API 响应类型 ===

/** 分页参数 */
export interface PaginationParams {
  limit?: number;
  offset?: number;
}

/** 分页响应 */
export interface PaginatedResponse<T> {
  items: T[];
  limit: number;
  offset: number;
  total: number;
}

/** BCS 前端资源清单 */
export interface BcsManifest {
  schema_version: number;
  env: string;
  bundles: BcsManifestBundle[];
}

/** BCS 前端资源包 */
export interface BcsManifestBundle {
  name: string;
  url: string;
}

/** 自定义协作模板参与者定义 */
export interface CollaborationTemplateParticipant {
  display_name: string;
  description: string;
  required: boolean;
}

/** 自定义协作模板列表项 */
export interface CollaborationTemplateSummary {
  id: string;
  name: string;
  description: string;
  participants: Record<string, CollaborationTemplateParticipant>;
  tags: string[];
  priority: number;
  available_languages: string[];
}

/** 自定义协作模板列表响应 */
export interface CollaborationTemplatesResponse {
  templates: CollaborationTemplateSummary[];
  tag_labels: Record<string, Record<string, string>>;
  default_language: string;
  supported_languages: string[];
}

/** 创建群聊响应 */
export interface CreateGroupResponse {
  context_injected: number;
  driver_bot: string;
  id: string;
  participants?: Array<
    | string
    | {
        bot_uuid: string;
        bot_name?: string;
        role?: string;
      }
  >;
  scene_group_id: string;
  scene_group_name: string;
  group_kind?: 'normal' | 'dm';
  group_strategy?: GroupStrategy;
}

/** 获取群详情响应 */
export interface GetGroupResponse {
  group_id?: string;
  id?: string;
  label: string | null;
  coordinator_bot?: string;
  driver_bot?: string;
  participants: Array<{
    bot_uuid: string;
    bot_name?: string;
    role: string;
    /** Actor 类型：human 或 bot */
    actor_kind?: 'human' | 'bot';
    /** 协作姿态/发言模式 */
    mode?: 'present' | 'absent' | 'auto' | 'muted';
  }>;
  created_at?: number;
  updated_at?: number;
  /** 群聊类型（429新增） */
  group_kind?: 'normal' | 'dm';
  /** 协作群策略 */
  group_strategy?: GroupStrategy;
  /** DM 群的对端标识（429新增） */
  dm_pair_key?: string;
  /** 群可见性 */
  visibility?: 'public' | 'private';
  /** 群主 Bot 的 owner 名称 */
  driver_bot_owner_name?: string;
}

/** 群消息响应 */
export interface GroupMessageResponse {
  id: string;
  sender: string;
  content: string;
  timestamp: number;
  message_type?: string;
  role: 'user' | 'assistant' | 'tool_result' | 'system';
  bot_name?: string;
  blocks?: any[];
  /** 同一轮 bot 回复共享的 run ID，用于消息聚合 */
  run_id?: string;
  metadata?: {
    stop_reason?: string;
    tool_calls?: Array<{
      arguments: Record<string, any>;
      tool_call_id: string;
      tool_name: string;
    }>;
    is_error?: boolean;
    result?: string;
    tool_call_id?: string;
    tool_name?: string;
    /** 新格式：工具调用参数直接放在 metadata 顶层 */
    arguments?: Record<string, any>;
  };
  historyMeta?: {
    agentId?: string;
    conversationRoundId?: string;
    plugin?: string;
    role?: 'user' | 'assistant' | 'toolResult';
    schemaVersion?: number;
    sessionKey?: string;
    storedAt?: number;
    assistantAggregation?: {
      assistantAggregationId?: string;
      assistantTextPreview?: string;
      linkedToolResultCount?: number;
      matchedBy?: string;
      toolCallCount?: number;
      toolCalls?: Array<{
        toolCallId: string;
        toolName: string;
      }>;
      continuationOfAssistantAggregationId?: string;
    };
    toolResult?: {
      assistantAggregation?: {
        assistantAggregationId?: string;
        linkedToolResultCount?: number;
        toolCallCount?: number;
        toolCalls?: Array<{
          toolCallId: string;
          toolName: string;
        }>;
      };
      detailsDropped?: boolean;
      isSynthetic?: boolean;
      toolCallId?: string;
      toolName?: string;
    };
  };
}

/** Bot 可见性/参与模式 */
export type BotVisibility = 'private' | 'protected' | 'public';

/** Bot 信息 */
export interface BotInfo {
  bot_uuid: string;
  capabilities: {
    name: string | null;
    summary: string | null;
    domains: string[];
    skills: string[];
    scopes: string[];
  };
  visibility?: BotVisibility;
  is_friend?: boolean | null;
  avatar_url?: string;
  dynamic_status?: DynamicStatus;
}

/** 群组信息 */
export interface GroupInfo {
  group_id: string;
  /** 公开群列表接口返回 id 而非 group_id */
  id?: string;
  label: string | null;
  context?: string;
  coordinator_bot: string;
  participants: Array<{
    bot_uuid: string;
    bot_name?: string;
    role: string;
    /** Actor 类型：human 或 bot */
    actor_kind?: 'human' | 'bot';
    /** 协作姿态/发言模式 */
    mode?: 'present' | 'absent' | 'auto' | 'muted';
  }>;
  created_at: number;
  updated_at: number;
  /** 群聊类型（429新增） */
  group_kind?: 'normal' | 'dm';
  /** 协作群策略 */
  group_strategy?: GroupStrategy;
  /** DM 群的对端标识（429新增） */
  dm_pair_key?: string;
  /** 协作群策略 - chat(自由聊天) 或 manager_worker(任务协作-主从模式) */
  group_strategy?: 'chat' | 'manager_worker';
  /** 主节点 Bot UUID（manager_worker 群） */
  master_bot?: string;
  /** 群可见性 */
  visibility?: 'public' | 'private';
  /** 群主 Bot UUID（公开群列表接口返回） */
  driver_bot?: string;
  /** 群主 Bot 名称（公开群列表接口返回） */
  driver_bot_name?: string;
  /** 群创建者标识（公开群列表接口返回） */
  originator?: string;
  /** 群创建者名称（公开群列表接口返回） */
  originator_name?: string;
  /** 群 owner 用户名（公开群列表接口返回） */
  owner_name?: string;
  /** 群主 Bot 的 owner 名称（公开群列表接口返回） */
  driver_bot_owner_name?: string;
  /** 群成员数量（公开群列表接口返回） */
  participant_count?: number;
  /** 群消息数量（公开群列表接口返回） */
  message_count?: number;
}

/** Bot 群组列表响应 */
export interface BotGroupsResponse {
  bot_uuid: string;
  groups: GroupInfo[];
  total: number;
  items?: GroupInfo[]; // 兼容不同返回格式
}

/**
 * 获取 BCS 前端资源清单
 * GET /bcnproxy/manifest
 */
export async function getManifest(options?: { [key: string]: any }) {
  return request<BcsManifest>('/bcnproxy/manifest', {
    method: 'GET',
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/**
 * 获取自定义协作模板列表
 * GET /bcnproxy/collaboration/templates
 */
export async function getCollaborationTemplates(options?: {
  [key: string]: any;
}) {
  return request<CollaborationTemplatesResponse>(
    '/bcnproxy/collaboration/templates',
    {
      method: 'GET',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 获取自定义协作模板 YAML
 * GET /bcnproxy/collaboration/templates/{template_id}?lang={lang}
 */
export async function getCollaborationTemplateYaml(
  params: { template_id: string; lang?: string },
  options?: { [key: string]: any },
) {
  const response = await request<string>(
    `/bcnproxy/collaboration/templates/${encodeURIComponent(
      params.template_id,
    )}`,
    {
      method: 'GET',
      params: params.lang ? { lang: params.lang } : undefined,
      responseType: 'text',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );

  return typeof response === 'string' ? response : String(response ?? '');
}

// === Bot 管理接口 ===

/**
 * 查询所有已注册 Bot
 * GET /bcnproxy/bots
 */
export async function getBots(
  params?: { onboarded?: boolean; limit?: number; offset?: number },
  options?: { [key: string]: any },
) {
  const queryParams: Record<string, any> = {};
  if (params?.onboarded !== undefined) queryParams.onboarded = params.onboarded;
  if (params?.limit !== undefined) queryParams.limit = params.limit;
  if (params?.offset !== undefined) queryParams.offset = params.offset;

  return request<BotInfo[]>('/bcnproxy/bots', {
    method: 'GET',
    params: queryParams,
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/**
 * 获取 Bot BCN 详情
 * GET /bcnproxy/bots/{bot_uuid}
 */
export interface GetBotBCNDetailResponse {
  bot_uuid: string;
  capabilities: {
    domains: string[];
    hidden: boolean;
    name: string | null;
    scopes: string[];
    skills: string[];
    summary: string | null;
  };
  visibility?: BotVisibility;
}

export async function getBotBCNDetail(
  params: { bot_uuid: string },
  options?: { [key: string]: any },
) {
  const { bot_uuid } = params;
  return request<GetBotBCNDetailResponse>(`/bcnproxy/bots/${bot_uuid}`, {
    method: 'GET',
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/**
 * 发现 Bot（可协作的 Bot 列表）
 * GET /bcnproxy/bots/discover
 */
export interface DiscoverBotsParams {
  q?: string;
  name?: string;
  visibility?: BotVisibility;
  collaborate_bot?: string;
  limit?: number;
  offset?: number;
}

export interface DiscoverBotsResponse {
  bots: BotInfo[];
  total: number;
  limit: number;
  offset: number;
  /** 追踪ID，用于埋点关联 */
  trace_id?: string;
  /** 查询类型 */
  type?: 'search' | 'recommend';
}

export async function discoverBots(
  params: DiscoverBotsParams = {},
  options?: { [key: string]: any },
) {
  const queryParams: Record<string, any> = {};
  if (params.q) queryParams.q = params.q;
  if (params.name) queryParams.name = params.name;
  if (params.visibility) queryParams.visibility = params.visibility;
  if (params.collaborate_bot)
    queryParams.collaborate_bot = params.collaborate_bot;
  if (params.limit) queryParams.limit = params.limit;
  if (params.offset) queryParams.offset = params.offset;

  return request<DiscoverBotsResponse>('/bcnproxy/bots/discover', {
    method: 'GET',
    params: queryParams,
    skipErrorHandler: true,
    ...(options || {}),
  });
}

// === Bot 可见性接口 ===

export interface GetBotVisibilityResponse {
  bot_uuid: string;
  visibility: BotVisibility;
}

export interface SetBotVisibilityParams {
  bot_uuid: string;
  visibility: BotVisibility;
}

/**
 * 获取 Bot 可见性
 * GET /bcnproxy/bots/{bot_uuid}/visibility
 */
export async function getBotVisibility(
  params: { bot_uuid: string },
  options?: { [key: string]: any },
) {
  const { bot_uuid } = params;
  return request<GetBotVisibilityResponse>(
    `/bcnproxy/bots/${bot_uuid}/visibility`,
    {
      method: 'GET',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 设置 Bot 可见性
 * PUT /bcnproxy/bots/{bot_uuid}/visibility
 */
export async function setBotVisibility(
  params: SetBotVisibilityParams,
  options?: { [key: string]: any },
) {
  const { bot_uuid, visibility } = params;
  return request<{
    success: boolean;
    error?: string;
    data?: GetBotVisibilityResponse;
  }>(`/bcnproxy/bots/${bot_uuid}/visibility`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    data: { visibility },
    skipErrorHandler: true,
    ...(options || {}),
  });
}

// === 好友关系接口 ===

/** 动态状态 */
export interface DynamicStatus {
  status: 'active' | 'offline';
  last_active_at?: number;
}

/**
 * 获取好友列表
 * GET /bcnproxy/bots/{bot_uuid}/friends
 */
export interface GetFriendsResponse {
  success?: boolean;
  friends?: Array<{
    bot_uuid: string;
    name: string;
    summary?: string | null;
    is_online?: boolean;
    dynamic_status?: DynamicStatus;
  }>;
  data?: Array<{
    bot_uuid: string;
    name: string;
    summary?: string | null;
    is_online?: boolean;
    dynamic_status?: DynamicStatus;
  }>;
  error?: string;
}

export async function getFriends(
  params: { bot_uuid: string },
  options?: { [key: string]: any },
) {
  const { bot_uuid } = params;
  return request<GetFriendsResponse>(`/bcnproxy/bots/${bot_uuid}/friends`, {
    method: 'GET',
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/**
 * 创建好友请求
 * POST /bcnproxy/friends/request
 */
export interface CreateFriendRequestParams {
  from_bot?: string;
  to_bot: string;
}

export interface CreateFriendRequestResponse {
  success?: boolean;
  id?: string;
  from_bot?: string;
  to_bot?: string;
  status?: string;
  error?: string;
  message?: string;
}

export async function createFriendRequest(
  params: CreateFriendRequestParams,
  options?: { [key: string]: any },
) {
  return request<CreateFriendRequestResponse>('/bcnproxy/friends/request', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: params,
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/**
 * 获取好友请求列表
 * GET /bcnproxy/friends/requests
 */
export interface GetFriendRequestsParams {
  bot_uuid?: string;
  direction?: 'received' | 'sent' | 'all';
  status?: 'pending' | 'accepted' | 'rejected';
}

export interface FriendRequestItem {
  id: string;
  from_bot: string;
  to_bot: string;
  status: string;
  created_at: number;
  updated_at: number;
  from_bot_name?: string;
  to_bot_name?: string;
}

export interface GetFriendRequestsResponse {
  success?: boolean;
  received?: FriendRequestItem[];
  sent?: FriendRequestItem[];
  data?: {
    received?: FriendRequestItem[];
    sent?: FriendRequestItem[];
  };
  error?: string;
}

export async function getFriendRequests(
  params: GetFriendRequestsParams = {},
  options?: { [key: string]: any },
) {
  const queryParams: Record<string, any> = {};
  if (params.bot_uuid) queryParams.bot_uuid = params.bot_uuid;
  if (params.direction) queryParams.direction = params.direction;
  if (params.status) queryParams.status = params.status;

  return request<GetFriendRequestsResponse>('/bcnproxy/friends/requests', {
    method: 'GET',
    params: queryParams,
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/**
 * 接受好友请求
 * POST /bcnproxy/friends/requests/{request_id}/accept
 */
export interface AcceptFriendRequestResponse {
  success?: boolean;
  friend?: {
    bot_uuid: string;
    bot_name: string;
  };
  error?: string;
}

export async function acceptFriendRequest(
  params: { request_id: string },
  options?: { [key: string]: any },
) {
  const { request_id } = params;
  return request<AcceptFriendRequestResponse>(
    `/bcnproxy/friends/requests/${request_id}/accept`,
    {
      method: 'POST',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 拒绝好友请求
 * POST /bcnproxy/friends/requests/{request_id}/reject
 */
export interface RejectFriendRequestResponse {
  success?: boolean;
  error?: string;
}

export async function rejectFriendRequest(
  params: { request_id: string },
  options?: { [key: string]: any },
) {
  const { request_id } = params;
  return request<RejectFriendRequestResponse>(
    `/bcnproxy/friends/requests/${request_id}/reject`,
    {
      method: 'POST',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

// === 群组管理接口（原有） ===

/**
 * 查询所有群
 * GET /bcnproxy/groups
 * 支持 group_kind 过滤（normal|dm|all），默认为 normal
 */
export async function getGroups(
  params?: PaginationParams & { group_kind?: 'normal' | 'dm' | 'all' },
  options?: { [key: string]: any },
) {
  const queryParams: Record<string, any> = {};
  if (params?.limit !== undefined) queryParams.limit = params.limit;
  if (params?.offset !== undefined) queryParams.offset = params.offset;
  if (params?.group_kind !== undefined)
    queryParams.group_kind = params.group_kind;

  return request<PaginatedResponse<GroupInfo>>('/bcnproxy/groups', {
    method: 'GET',
    params: queryParams,
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/**
 * 查询公开群列表
 * GET /bcnproxy/groups?visibility=public
 */
export async function getPublicGroups(
  params?: {
    offset?: number;
    limit?: number;
    label?: string;
    group_kind?: 'normal' | 'dm' | 'all';
  },
  options?: { [key: string]: any },
) {
  const queryParams: Record<string, any> = { visibility: 'public' };
  if (params?.offset !== undefined) queryParams.offset = params.offset;
  if (params?.limit !== undefined) queryParams.limit = params.limit;
  if (params?.label !== undefined) queryParams.label = params.label;
  if (params?.group_kind !== undefined)
    queryParams.group_kind = params.group_kind;

  return request<PaginatedResponse<GroupInfo>>('/bcnproxy/groups', {
    method: 'GET',
    params: queryParams,
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/**
 * 获取 Bot 所在的所有群
 * GET /bcnproxy/bots/{bot_uuid}/groups
 *
 * 429新增：支持 group_kind 参数过滤（normal|dm|all），默认为 normal
 */
export async function getBotGroups(
  params: {
    bot_uuid: string;
    limit?: number;
    offset?: number;
    group_kind?: 'normal' | 'dm' | 'all';
    // 搜索关键词, （匹配 group_id / label）
    q?: string; // 搜索关键词
  },
  options?: { [key: string]: any },
) {
  const { bot_uuid, limit, offset, group_kind, q } = params;

  const queryParams: Record<string, any> = {};
  if (limit !== undefined) queryParams.limit = limit;
  if (offset !== undefined) queryParams.offset = offset;
  if (group_kind !== undefined) queryParams.group_kind = group_kind;
  if (q !== undefined) queryParams.q = q;

  const queryString = Object.keys(queryParams).length
    ? `?${new URLSearchParams(queryParams).toString()}`
    : '';

  return request<BotGroupsResponse>(
    `/bcnproxy/bots/${bot_uuid}/groups${queryString}`,
    {
      method: 'GET',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 创建群聊
 * POST /bcnproxy/groups
 */
export async function createGroup(
  params: CreateGroupParams,
  options?: { [key: string]: any },
) {
  return request<CreateGroupResponse & { success?: boolean; error?: string }>(
    '/bcnproxy/groups',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      data: params,
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 获取群详情
 * GET /bcnproxy/groups/{group_id}
 */
export async function getGroup(
  params: { group_id: string },
  options?: { [key: string]: any },
) {
  const { group_id } = params;
  return request<GetGroupResponse & { success?: boolean; error?: string }>(
    `/bcnproxy/groups/${group_id}`,
    {
      method: 'GET',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 获取群历史消息
 * GET /bcnproxy/groups/{group_id}/messages
 */
export async function getGroupMessages(
  params: {
    group_id: string;
    cursor?: string;
    limit?: number;
    view_bot_id?: string;
  },
  options?: { [key: string]: any },
) {
  const { group_id, ...restParams } = params;

  return request<GroupMessageResponse[]>(
    `/bcnproxy/groups/${group_id}/messages`,
    {
      method: 'GET',
      params: restParams,
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 删除群组
 * DELETE /bcnproxy/groups/{group_id}?bot_id=xxx
 */
export async function deleteGroup(
  params: { group_id: string; bot_id: string },
  options?: { [key: string]: any },
) {
  const { group_id, bot_id } = params;
  return request<{ success?: boolean; error?: string }>(
    `/bcnproxy/groups/${group_id}?bot_id=${bot_id}`,
    {
      method: 'DELETE',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

// === Group 可见性接口 ===

export interface SetGroupVisibilityParams {
  group_id: string;
  visibility: 'public' | 'private';
}

export interface SetGroupVisibilityResponse {
  updated: boolean;
  group_id: string;
  visibility: 'public' | 'private';
  changed_by: string;
}

/**
 * 修改 Group 可见性
 * PUT /bcnproxy/groups/{group_id}/visibility
 */
export async function setGroupVisibility(
  params: SetGroupVisibilityParams,
  options?: { [key: string]: any },
) {
  const { group_id, visibility } = params;
  return request<SetGroupVisibilityResponse>(
    `/bcnproxy/groups/${group_id}/visibility`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      data: { visibility },
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

// === Group 成员管理接口 ===

/** Group 成员角色（与 SessionMemberRole 同源） */
export type GroupMemberRoleApi =
  | 'driver'
  | 'consultant'
  | 'observer'
  | 'manager'
  | 'worker';

/** 添加 Group 成员响应 */
export interface AddGroupMemberResponse {
  added: boolean;
  /** 文档示例字段名为 session_id,实际指 group_id */
  session_id?: string;
  group_id?: string;
  member: {
    bot_uuid: string;
    role: GroupMemberRoleApi;
  };
}

/**
 * 添加 Group 成员
 * POST /bcnproxy/groups/{group_id}/members
 *
 * - body: { bot_uuid, role? }; role 不填时由后端按群策略给默认值
 *   （Chat: consultant；ManagerWorker: worker）
 * - 仅 driver/coordinator 有权操作；DM 群禁止添加
 */
export async function addGroupMember(
  params: {
    group_id: string;
    bot_uuid: string;
    role?: GroupMemberRoleApi;
  },
  options?: { [key: string]: any },
) {
  const { group_id, bot_uuid, role } = params;
  const data: Record<string, string> = { bot_uuid };
  if (role) data.role = role;
  return request<AddGroupMemberResponse>(
    `/bcnproxy/groups/${group_id}/members`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data,
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/** 删除 Group 成员响应 */
export interface DeleteGroupMemberResponse {
  removed: boolean;
  group_id: string;
  removed_bot_uuid: string;
}

/**
 * 删除 Group 成员
 * DELETE /bcnproxy/groups/{group_id}/members/{bot_uuid}
 *
 * - 仅 driver/coordinator 有权操作；DM 群禁止移除；禁止移除 driver 自身
 */
export async function deleteGroupMember(
  params: { group_id: string; bot_uuid: string },
  options?: { [key: string]: any },
) {
  const { group_id, bot_uuid } = params;
  return request<DeleteGroupMemberResponse>(
    `/bcnproxy/groups/${group_id}/members/${bot_uuid}`,
    {
      method: 'DELETE',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * Bot Onboard / 加入或移出协作网络
 * POST /bcnproxy/admin/bots/onboard
 * - hidden=false: 加入协作网络（取消隐藏）
 * - hidden=true: 移出协作网络（隐藏）
 */
export async function onboardBot(
  body: {
    /** Bot 唯一标识（必须已存在） */
    bot_id: string;
    /** Bot 显示名称 */
    name: string;
    /** Bot 简介 */
    summary: string;
    /** 是否隐藏 */
    hidden?: boolean;
  },
  options?: { [key: string]: any },
) {
  return request<any>('/bcnproxy/admin/bots/onboard', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    skipErrorHandler: true,
    ...(options || {}),
  });
}

// === Bot 批量查询接口 ===

/** 批量查询 Bot 状态请求参数 */
export interface QueryBotsParams {
  bot_uuids: string[];
}

/** 批量查询 Bot 状态响应 */
export interface QueryBotsResponse {
  bot_uuid: string;
  capabilities: {
    domains: string[];
    hidden: boolean;
    name: string | null;
    scopes: string[];
    skills: string[];
    summary: string | null;
  };
  visibility: BotVisibility;
  /** Actor 在线状态 */
  status?: 'online' | 'hidden' | 'offline';
  /** 动态状态：active 或 offline，用于展示在线/离线状态点 */
  dynamic_status?: DynamicStatus;
}

/**
 * 批量查询 Bot 状态
 * POST /bcnproxy/bots/query
 */
export async function queryBots(
  params: QueryBotsParams,
  options?: { [key: string]: any },
) {
  return request<QueryBotsResponse[]>('/bcnproxy/bots/query', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: params,
    skipErrorHandler: true,
    ...(options || {}),
  });
}

// === 我的 Bot 列表接口 ===

/** 我的 Bot 信息 */
export interface MyBotInfo {
  bot_uuid: string;
  bot_id: string;
  entity_id: string;
  capabilities: {
    name: string | null;
    summary: string | null;
    domains: string[];
    skills: string[];
    scopes: string[];
  };
  visibility: BotVisibility;
  is_online?: boolean;
  avatar_url?: string;
  /** Actor 类型：human 或 bot */
  actor_kind?: 'human' | 'bot';
  /** Actor 在线状态 */
  status?: 'online' | 'hidden' | 'offline';
  /** 动态状态：active 或 offline，用于展示在线/离线状态点 */
  dynamic_status?: DynamicStatus;
}

export interface GetMyBotsParams {
  active_only?: boolean;
}

/** 获取我的 Bot 列表响应 */
export interface GetMyBotsResponse {
  items: MyBotInfo[];
  total?: number;
}

/**
 * 获取我的 Bot 列表
 * GET /bcnproxy/bots/my
 */
export async function getMyBots(
  params: GetMyBotsParams = {},
  options?: { [key: string]: any },
) {
  return request<GetMyBotsResponse>('/bcnproxy/bots/my', {
    method: 'GET',
    params: {
      active_only: params.active_only ?? false,
    },
    skipErrorHandler: true,
    ...(options || {}),
  });
}

// === Human Actor 接口 ===

/**
 * 更新参与者协作姿态/发言模式
 * PUT /bcnproxy/groups/{group_id}/participants/{actor_id}/mode
 *
 * Human: present（参与）/ absent（旁观）
 * Bot: auto（自动）/ muted（禁言）
 */
export interface UpdateParticipantModeParams {
  group_id: string;
  actor_id: string;
  mode: 'present' | 'absent' | 'auto' | 'muted';
}

export interface UpdateParticipantModeResponse {
  success?: boolean;
  error?: string;
  data?: {
    group_id: string;
    actor_id: string;
    mode: string;
    updated_at: number;
  };
  // 兼容旧格式（直接返回在顶层）
  group_id?: string;
  actor_id?: string;
  mode?: string;
  updated_at?: number;
}

export async function updateParticipantMode(
  params: UpdateParticipantModeParams,
  options?: { [key: string]: any },
) {
  const { group_id, actor_id, mode } = params;
  return request<UpdateParticipantModeResponse>(
    `/bcnproxy/groups/${group_id}/participants/${actor_id}/mode`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      data: { mode },
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 更新 Actor 在线状态
 * PUT /bcnproxy/actors/{actor_id}/status
 *
 * status: online / hidden
 */
export interface UpdateActorStatusParams {
  actor_id: string;
  status: 'online' | 'hidden';
}

export interface UpdateActorStatusResponse {
  success?: boolean;
  error?: string;
  data?: {
    actor_id: string;
    status: string;
    updated_at: number;
  };
}

export async function updateActorStatus(
  params: UpdateActorStatusParams,
  options?: { [key: string]: any },
) {
  const { actor_id, status } = params;
  return request<UpdateActorStatusResponse>(
    `/bcnproxy/actors/${actor_id}/status`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      data: { status },
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

// === Human Actor 自助修复接口 ===

/** 修复当前 Human Actor 信息响应 */
export interface RepairHumanActorInfoResult {
  ok: boolean;
  staff_no?: string | null;
  nick_name?: string | null;
  actor_uuid?: string | null;
  previous_name?: string | null;
  current_name?: string | null;
  name_repaired: boolean;
  skipped_reason?: string | null;
  error?: string | null;
}

/** 确保当前用户 Human Actor 存在响应 */
export interface EnsureCurrentHumanActorResult {
  actor_uuid: string;
  human_created: boolean;
  matched_bots: string[];
  edges_created: number;
  edges_upgraded: number;
  failed_bots: string[];
  errors: string[];
}

/** 初始化 Human Actor 响应，兼容历史包装字段 */
export type EnsureHumanResponse = EnsureCurrentHumanActorResult & {
  success?: boolean;
  error?: string;
  bot_uuid?: string;
};

/**
 * 修复当前 Human Actor 信息
 * GET /bcnproxy/me/repair-info
 */
export async function repairHumanActorInfo(options?: { [key: string]: any }) {
  return request<RepairHumanActorInfoResult>('/bcnproxy/me/repair-info', {
    method: 'GET',
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/**
 * 初始化 Human Actor
 * POST /bcnproxy/me/ensure-human
 */
export async function ensureHuman(options?: { [key: string]: any }) {
  return request<EnsureHumanResponse>('/bcnproxy/me/ensure-human', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    skipErrorHandler: true,
    ...(options || {}),
  });
}

// === 当前用户信息接口 ===

/** 获取当前 Human Actor 信息响应 */
export interface GetMeResponse {
  /** Actor UUID，格式为 human_{staff_no} */
  actor_uuid: string;
  /** 花名 */
  nick_name: string;
  /** 工号 */
  staff_no: string;
}

/**
 * 获取当前 Human Actor 信息（用于独立页面获取 userId）
 * GET /bcnproxy/me
 */
export async function getMe(options?: { [key: string]: any }) {
  return request<GetMeResponse>('/bcnproxy/me', {
    method: 'GET',
    skipErrorHandler: true,
    ...(options || {}),
  });
}

// === Bot 注册 token 接口 ===

/** 获取 Bot 注册 token 响应 */
export interface RegisterTokenResponse {
  /** 注册 token（用于本地 Bot 接入指令） */
  token: string;
  /** 过期时间（毫秒时间戳） */
  expires_at: number;
  /** 备注说明 */
  note?: string;
}

/**
 * 获取当前用户的 Bot 注册 token（产品首页接入指令动态注入用）
 * GET /bcnproxy/register/token
 * 未登录返回 401（由调用方 try/catch 兜底，显示占位 token）。
 */
export async function getRegisterToken(options?: { [key: string]: any }) {
  return request<RegisterTokenResponse>('/bcnproxy/register/token', {
    method: 'GET',
    skipErrorHandler: true,
    ...(options || {}),
  });
}

// === Session 管理接口 ===

/** Session 信息（API 响应） */
export interface SessionInfoResponse {
  session_id: string;
  group_id: string;
  session_kind: string;
  session_title: string | null;
  status: string;
  participants: Array<{
    actor_id?: string;
    actor_kind?: 'bot' | 'human';
    bot_uuid?: string;
    bot_name?: string;
    role?: string;
    type?: string;
    mode?: 'present' | 'absent' | 'auto' | 'muted';
  }>;
  activation_count?: number;
  input?:
    | string
    | {
        query?: string;
      };
  output?: string;
  error_message?: string;
  callback_status?: string;
  created_at: number;
  updated_at: number;
  completed_at?: number;
  created_by?: string;
}

/** Session 列表响应 */
export interface SessionListResponse {
  items: SessionInfoResponse[];
  sessions?: SessionInfoResponse[];
  total?: number;
  limit: number;
  offset: number;
}

/**
 * 获取群组 Session 列表
 * GET /bcnproxy/groups/{group_id}/sessions
 * 支持 q 参数搜索会话标题
 */
export async function getGroupSessions(
  params: {
    group_id: string;
    limit?: number;
    offset?: number;
    status?: string;
    session_id?: string;
    q?: string; // 搜索关键词（按会话标题搜索）
    participant?: string; // 当前视角 Bot 的 bot_uuid
  },
  options?: { [key: string]: any },
) {
  const { group_id, limit, offset, status, session_id, q, participant } =
    params;

  const queryParams: Record<string, any> = {};
  if (limit !== undefined) queryParams.limit = limit;
  if (offset !== undefined) queryParams.offset = offset;
  if (status !== undefined) queryParams.status = status;
  if (session_id !== undefined) queryParams.session_id = session_id;
  if (q !== undefined) queryParams.q = q;
  if (participant !== undefined) queryParams.participant = participant;

  const queryString = Object.keys(queryParams).length
    ? `?${new URLSearchParams(queryParams).toString()}`
    : '';

  return request<SessionListResponse>(
    `/bcnproxy/groups/${group_id}/sessions${queryString}`,
    {
      method: 'GET',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 创建 Session
 * POST /bcnproxy/groups/{group_id}/sessions
 */
export async function createGroupSession(
  params: {
    group_id: string;
    session_kind: string;
    session_title?: string;
    created_by?: string;
    input?: {
      query: string;
    };
  },
  options?: { [key: string]: any },
) {
  const { group_id, ...data } = params;
  return request<CreateSessionResponse>(
    `/bcnproxy/groups/${group_id}/sessions`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data,
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/** Session 创建响应 */
export interface CreateSessionResponse {
  session_id: string;
  group_id: string;
  session_kind: string;
  session_title: string;
  status: string;
  created_at: number;
}

/**
 * 获取 Session 详情
 * GET /bcnproxy/sessions/{session_id}
 */
export async function getSessionDetail(
  params: { session_id: string },
  options?: { [key: string]: any },
) {
  return request<SessionInfoResponse>(
    `/bcnproxy/sessions/${params.session_id}`,
    {
      method: 'GET',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 获取 Session 消息
 * GET /bcnproxy/sessions/{session_id}/messages
 */
export async function getSessionMessages(
  params: {
    session_id: string;
    cursor?: string;
    limit?: number;
    view_bot_id?: string;
  },
  options?: { [key: string]: any },
) {
  const { session_id, ...queryParams } = params;
  return request<GroupMessageResponse[]>(
    `/bcnproxy/sessions/${session_id}/messages`,
    {
      method: 'GET',
      params: queryParams,
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 更新 Session 成员模式（mode 切换：present/absent/auto/muted）
 * PATCH /bcnproxy/sessions/{session_id}/members/{actor_id}
 *
 * 用于「当前用户进/出会话」「Bot 自动/禁言」等模式切换；
 * 添加/移除成员请使用 addSessionMember / deleteSessionMember。
 */
export async function updateSessionMember(
  params: {
    session_id: string;
    actor_id: string;
    mode: 'present' | 'absent' | 'auto' | 'muted';
  },
  options?: { [key: string]: any },
) {
  const { session_id, actor_id, mode } = params;
  return request<{ success?: boolean; error?: string }>(
    `/bcnproxy/sessions/${session_id}/members/${actor_id}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      data: { mode },
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 添加 Session 成员
 * POST /bcnproxy/sessions/{session_id}/members
 *
 * - body: { bot_uuid, role? }; role 不填时由后端按群策略给默认值
 *   （Chat: consultant；ManagerWorker: worker）
 * - 响应：完整 Session 对象（更新后的 participants）
 */
export type SessionMemberRole =
  | 'driver'
  | 'consultant'
  | 'observer'
  | 'manager'
  | 'worker';

export async function addSessionMember(
  params: {
    session_id: string;
    bot_uuid: string;
    role?: SessionMemberRole;
  },
  options?: { [key: string]: any },
) {
  const { session_id, bot_uuid, role } = params;
  const data: Record<string, string> = { bot_uuid };
  if (role) data.role = role;
  return request<SessionInfoResponse>(
    `/bcnproxy/sessions/${session_id}/members`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data,
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 删除 Session
 * DELETE /bcnproxy/sessions/{session_id}?bot_id={bot_id}
 */
export async function deleteSession(
  params: { session_id: string; bot_id: string },
  options?: { [key: string]: any },
) {
  const { session_id, bot_id } = params;
  return request<void>(`/bcnproxy/sessions/${session_id}`, {
    method: 'DELETE',
    params: { bot_id },
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/**
 * 删除 Session 成员
 * DELETE /bcnproxy/sessions/{session_id}/members/{bot_uuid}
 *
 * - 物理移除该成员（不同于 PATCH mode='absent' 仅切换模式）
 * - 响应：完整 Session 对象（更新后的 participants）
 */
export async function deleteSessionMember(
  params: { session_id: string; bot_uuid: string },
  options?: { [key: string]: any },
) {
  const { session_id, bot_uuid } = params;
  return request<SessionInfoResponse>(
    `/bcnproxy/sessions/${session_id}/members/${bot_uuid}`,
    {
      method: 'DELETE',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 更新 Session 标题
 * PATCH /bcnproxy/sessions/{session_id}
 */
export async function updateSessionTitle(
  params: { session_id: string; session_title: string | null },
  options?: { [key: string]: any },
) {
  return request<{ success?: boolean; error?: string }>(
    `/bcnproxy/sessions/${params.session_id}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      data: { session_title: params.session_title },
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

// === 群消息响应类型 ===

/** 群消息响应 */
export interface GroupMessageResponse {
  id: string;
  sender: string;
  content: string;
  timestamp: number;
  message_type?: string;
  role: 'user' | 'assistant' | 'tool_result' | 'system';
  bot_name?: string;
  blocks?: any[];
  /** 同一轮 bot 回复共享的 run ID，用于消息聚合 */
  run_id?: string;
  metadata?: {
    stop_reason?: string;
    tool_calls?: Array<{
      arguments: Record<string, any>;
      tool_call_id: string;
      tool_name: string;
    }>;
    is_error?: boolean;
    result?: string;
    tool_call_id?: string;
    tool_name?: string;
    /** 新格式：工具调用参数直接放在 metadata 顶层 */
    arguments?: Record<string, any>;
  };
  historyMeta?: {
    agentId?: string;
    conversationRoundId?: string;
    plugin?: string;
    role?: 'user' | 'assistant' | 'toolResult';
    schemaVersion?: number;
    sessionKey?: string;
    storedAt?: number;
    assistantAggregation?: {
      assistantAggregationId?: string;
      assistantTextPreview?: string;
      linkedToolResultCount?: number;
      matchedBy?: string;
      toolCallCount?: number;
      toolCalls?: Array<{
        toolCallId: string;
        toolName: string;
      }>;
      continuationOfAssistantAggregationId?: string;
    };
    toolResult?: {
      assistantAggregation?: {
        assistantAggregationId?: string;
        linkedToolResultCount?: number;
        toolCallCount?: number;
        toolCalls?: Array<{
          toolCallId: string;
          toolName: string;
        }>;
      };
      detailsDropped?: boolean;
      isSynthetic?: boolean;
      toolCallId?: string;
      toolName?: string;
    };
  };
}

// === 邀请链接相关类型 ===

/** 生成邀请链接响应 */
export interface InviteLinkResponse {
  invite_token: string;
  expires_at: number;
  join_url: string;
}

/** 加入响应 */
export interface JoinResponse {
  joined: boolean;
  already_member: boolean;
  target_type: 'group' | 'session';
  target_id: string;
  actor_id: string;
  error?: string;
}

// === 邀请链接 API ===

/**
 * 生成 Group 级邀请链接
 * POST /bcnproxy/groups/{group_id}/invite-link
 */
export async function createGroupInviteLink(
  params: { group_id: string; ttl_seconds?: number },
  options?: { [key: string]: any },
) {
  const { group_id, ...data } = params;
  return request<InviteLinkResponse & { error?: string }>(
    `/bcnproxy/groups/${group_id}/invite-link`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: data.ttl_seconds ? { ttl_seconds: data.ttl_seconds } : {},
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 生成 Session 级邀请链接
 * POST /bcnproxy/sessions/{session_id}/invite-link
 */
export async function createSessionInviteLink(
  params: { session_id: string; ttl_seconds?: number },
  options?: { [key: string]: any },
) {
  const { session_id, ...data } = params;
  return request<InviteLinkResponse & { error?: string }>(
    `/bcnproxy/sessions/${session_id}/invite-link`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: data.ttl_seconds ? { ttl_seconds: data.ttl_seconds } : {},
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/**
 * 使用邀请 token 加入 Group 或 Session
 * POST /bcnproxy/{type}/join/{invite_token}
 * @param type - "groups" 或 "sessions"
 */
export async function joinByInviteToken(
  params: { type: 'groups' | 'sessions'; invite_token: string },
  options?: { [key: string]: any },
) {
  const { type, invite_token } = params;
  return request<JoinResponse & { error?: string }>(
    `/bcnproxy/${type}/join/${invite_token}`,
    {
      method: 'POST',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}
