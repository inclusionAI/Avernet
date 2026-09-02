import { isAceLoginResponse } from '../aceLoginBody';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage } from '../types';

export type GroupStrategy = 'chat' | 'manager_worker' | 'state_machine';
export type DeliveryPolicy = { bot_final_delivery: 'send_to_driver' | 'inject_observers' };
export type GroupVisibility = 'private' | 'public';
export type GroupStatus = 'active' | 'dissolved' | 'purge' | 'closed' | 'inactive' | 'completed' | 'error';

export interface GroupParticipantDto {
  actor_id: string;
  actor_kind: 'human' | 'bot';
  name?: string;
  role: 'driver' | 'consultant' | 'manager' | 'worker' | 'observer';
  mode: 'auto' | 'muted' | 'absent' | 'present';
}
export interface GroupCollaborationChat {
  strategy: 'chat';
  delivery_policy: DeliveryPolicy;
}
export interface GroupCollaborationManagerWorker {
  strategy: 'manager_worker';
}
export interface GroupCollaborationStateMachine {
  strategy: 'state_machine';
  /** definition：读取场景下发 definition_id+version；创建场景下发 content_yaml（仅此字段，后端 definition 不认 participant_bindings）。 */
  definition: {
    definition_id?: string;
    version?: number;
    content_yaml?: string;
  };
  /** 与 definition 同级的角色绑定（创建场景下发）；对应后端 StateMachineConfiguration.participant_bindings。 */
  participant_bindings?: Array<{ binding: string; actor_ids: string[] }>;
}
export type GroupCollaboration =
  | GroupCollaborationChat
  | GroupCollaborationManagerWorker
  | GroupCollaborationStateMachine;

export interface GroupParticipantInput {
  actor_id: string;
  role: GroupParticipantDto['role'];
}

export interface GroupDetailData {
  group_id: string;
  version: number;
  kind: 'normal' | 'dm';
  status: GroupStatus;
  visibility: GroupVisibility;
  originator_actor_id: string;
  participants: GroupParticipantDto[];
  driver_bot_uuid: string;
  collaboration: GroupCollaboration;
  name?: string;
  created_at: number;
  updated_at: number;
  // list 场景只暴露的字段
  membership?: 'direct' | 'session_only';
  participant_count?: number;
  /** 创建群时后端同步生成的初始会话 ID；详情接口可能不返回。 */
  initial_session_id?: string;
  /** 创建群时同步启动的 Driver/Manager run；详情接口可能不返回。 */
  initial_run?: {
    run_id: string;
    bot_uuid: string;
    activity_kind: 'group_bootstrap';
    state: 'running' | 'failed';
    started_at: string;
  };
}
export interface GroupCreateChatBody {
  group_kind: 'normal';
  name?: string;
  context?: string;
  participants: GroupParticipantInput[];
  driver_bot_uuid: string;
  originator: string;
  collaboration: GroupCollaborationChat;
}
export interface GroupCreateManagerWorkerBody {
  group_kind: 'normal';
  name?: string;
  context?: string;
  participants: GroupParticipantInput[];
  driver_bot_uuid: string;
  originator: string;
  collaboration: GroupCollaborationManagerWorker;
}
export interface GroupCreateStateMachineBody {
  group_kind: 'normal';
  name?: string;
  context?: string;
  participants: GroupParticipantInput[];
  driver_bot_uuid: string;
  originator: string;
  collaboration: GroupCollaborationStateMachine;
}
export type CreateGroupBody = GroupCreateChatBody | GroupCreateManagerWorkerBody | GroupCreateStateMachineBody;
export interface GroupUpdateBody {
  name?: string;
  visibility?: GroupVisibility;
  delivery_policy?: DeliveryPolicy;
}

export interface ListGroupsParams {
  view_bot_id?: string;
  q?: string;
  /** 群列表查询使用 kind；公开协作群目录固定为 normal。 */
  kind?: 'normal' | 'dm';
  /** 兼容旧调用方的查询别名；公开协作群目录不使用该字段。 */
  group_kind?: 'normal' | 'dm';
  /** 公开协作群目录固定传 public；普通工作区列表可不传。 */
  visibility?: GroupVisibility;
  strategy?: GroupStrategy;
  offset?: number;
  limit?: number;
  membership?: 'direct' | 'session_only';
}

export interface ListPublicGroupsParams {
  q?: string;
  offset?: number;
  limit?: number;
}

export type PublicGroupCatalogErrorCode = 'unauthenticated' | 'protocol_error';

export class PublicGroupCatalogError extends Error {
  constructor(public readonly code: PublicGroupCatalogErrorCode, message: string) {
    super(message);
    this.name = 'PublicGroupCatalogError';
  }
}

export interface PublicGroupCatalogResponse {
  code: 20000;
  message?: string;
  data: {
    items: GroupDetailData[];
    total: number;
  };
  request_id?: string;
}

// Endpoint 常量（保留供外部协议层测试与诊断工具使用）。
export const COLLABORATION_GROUP_ENDPOINTS = {
  list: '/openapi/v1/collaboration/groups',
  detail: (group_id: string) => `/openapi/v1/collaboration/groups/${group_id}`,
  sessions: (group_id: string) => `/openapi/v1/collaboration/groups/${group_id}/sessions`,
  participants: (group_id: string) => `/openapi/v1/collaboration/groups/${group_id}/participants`,
  participant: (group_id: string, actor_id: string) =>
    `/openapi/v1/collaboration/groups/${group_id}/participants/${actor_id}`,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function validatePublicGroupCatalogResponse(value: unknown): PublicGroupCatalogResponse {
  if (isAceLoginResponse(value)) {
    throw new PublicGroupCatalogError('unauthenticated', 'Public group catalog request requires authentication');
  }
  if (!isRecord(value) || value.code !== 20000) {
    throw new PublicGroupCatalogError('protocol_error', 'Unexpected public group catalog business response');
  }
  if (!isRecord(value.data) || !Array.isArray(value.data.items) || typeof value.data.total !== 'number') {
    throw new PublicGroupCatalogError('protocol_error', 'Invalid public group catalog page response');
  }
  return value as unknown as PublicGroupCatalogResponse;
}

// 查询协作群组列表。
// 不注入 user_id：该接口按 identity 视图(view_bot_id)与 membership 过滤，后端以会话态签别身份，
// query 无需 user_id。默认注入依赖 identityStore.currentIdentityId，身份未就绪/被清空时
// user_id 时有时无（正是本接口 user_id 不稳定的原因）。显式关闭注入，与 listPublicGroups 一致。
export async function listGroups(params: ListGroupsParams) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<GroupDetailData>>>('/openapi/v1/collaboration/groups', {
    method: 'GET',
    params: params as Record<string, unknown>,
    injectUserId: false,
  });
}

// 查询公开协作群目录（分页）。专用路由 GET /openapi/v1/collaboration/public-groups，
// 参数仅 q（名称模糊匹配）/ offset / limit；响应为 {code:20000,data:{items,total}} 信封。
// 不传 view_bot_id、不注入 user_id（公开目录无需身份视图）。
export async function listPublicGroups(
  params: ListPublicGroupsParams = {},
  signal?: AbortSignal,
): Promise<PublicGroupCatalogResponse> {
  const response = await backendRequest<unknown>('/openapi/v1/collaboration/public-groups', {
    method: 'GET',
    params: params as Record<string, unknown>,
    injectUserId: false,
    signal,
  });
  return validatePublicGroupCatalogResponse(response);
}

// 查询协作群组详情。
export async function getGroup(group_id: string) {
  return backendRequest<BackendApiEnvelope<GroupDetailData>>(`/openapi/v1/collaboration/groups/${group_id}`, {
    method: 'GET',
    injectUserId: false,
  });
}

// 创建协作群组。
export async function createGroup(body: CreateGroupBody) {
  return backendRequest<BackendApiEnvelope<GroupDetailData>>('/openapi/v1/collaboration/groups', {
    method: 'POST',
    data: body,
    injectUserId: false,
  });
}

// 更新协作群组。
export async function updateGroup(group_id: string, patch: GroupUpdateBody) {
  return backendRequest<BackendApiEnvelope<GroupDetailData>>(`/openapi/v1/collaboration/groups/${group_id}`, {
    method: 'PATCH',
    data: patch,
    injectUserId: false,
  });
}

// 删除协作群组。
export async function deleteGroup(group_id: string) {
  return backendRequest<BackendApiEnvelope<{ deleted: boolean }>>(`/openapi/v1/collaboration/groups/${group_id}`, {
    method: 'DELETE',
    injectUserId: false,
  });
}

// 新增群组成员。
export async function addGroupParticipant(group_id: string, actor_id: string) {
  return backendRequest<BackendApiEnvelope<void>>(`/openapi/v1/collaboration/groups/${group_id}/participants`, {
    method: 'POST',
    data: { actor_id },
    injectUserId: false,
  });
}

// 移除群组成员。
export async function deleteGroupParticipant(group_id: string, actor_id: string) {
  return backendRequest<BackendApiEnvelope<void>>(
    `/openapi/v1/collaboration/groups/${group_id}/participants/${actor_id}`,
    { method: 'DELETE', injectUserId: false },
  );
}

// 协作群组下的会话列表（委托给 sessionController，统一在 sessions 接口实现）。
export { listGroupSessions } from './sessionController';
// 创建群组会话（sessionController 中实现为 createSession，群组侧以 createGroupSession 别名导出）。
export { createSession as createGroupSession } from './sessionController';
