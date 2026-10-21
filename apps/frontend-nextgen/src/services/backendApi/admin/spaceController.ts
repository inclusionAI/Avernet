// 空间管理协议层 Controller。空间列表/创建/成员增删改/申请加入。
// 契约对齐 clawweb=Avernet（router prefix /openapi/v1/bots/spaces）：user_id 为 query 必填
// （UserIdDep/ActingCallerDep，缺失 422）；分页用 page_no；body 用 member_user_id/reason。
// user_id 由 Service 经 resolveUserId(activeIdentityId) 注入，Controller 不自取。

import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

export type SpaceDto = BackendUnknownRecord;
export type SpaceMemberDto = BackendUnknownRecord;

export const SPACE_ENDPOINTS = {
  list: '/openapi/v1/bots/spaces',
  create: '/openapi/v1/bots/spaces/create',
  detail: (space_id: number | string) => `/openapi/v1/bots/spaces/${space_id}`,
  members: (space_id: number | string) => `/openapi/v1/bots/spaces/${space_id}/members`,
  member: (space_id: number | string, member_user_id: string) =>
    `/openapi/v1/bots/spaces/${space_id}/members/${member_user_id}`,
  memberRole: (space_id: number | string, member_user_id: string) =>
    `/openapi/v1/bots/spaces/${space_id}/members/${member_user_id}/role`,
  joinRequests: (space_id: number | string) => `/openapi/v1/bots/spaces/${space_id}/join-requests`,
  personalInitialize: '/openapi/v1/bots/spaces/personal/initialize',
};

/** 操作者 user_id query（后端 UserIdDep 强制；Service 经 resolveUserId 注入）。 */
export interface SpaceUserIdParams {
  user_id: string;
  /** 操作者花名（创建空间 / 申请加入时记录操作者展示名；可选，仅 create、join-requests 传）。 */
  user_name?: string;
  [key: string]: unknown;
}

export interface SpaceListParams extends SpaceUserIdParams {
  [key: string]: unknown;
  keyword?: string;
  space_type?: string; // TEAM / PERSONAL
  page_no?: number;
  page_size?: number;
  /** 可访问范围：accessible=仅返回当前账号已加入空间（后端过滤，替代前端 filterJoinedSpaces）。 */
  scope?: string;
}

export interface SpaceMemberListParams extends SpaceUserIdParams {
  [key: string]: unknown;
  keyword?: string;
  page_no?: number;
  page_size?: number;
}

export interface AddSpaceMemberBody {
  member_user_id: string;
  role?: 'ADMIN' | 'MEMBER';
  /** 被加成员花名（来自员工目录搜索 nickName，与 member_user_id 配对）；传入后成员表可直接展示花名而非工号。 */
  member_user_name?: string;
}

export interface UpdateMemberRoleBody {
  role: 'ADMIN' | 'MEMBER';
}

export interface RequestJoinBody {
  reason: string;
}

// 查询平台空间列表。
export function listSpaces(params: SpaceListParams) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<SpaceDto>>>(SPACE_ENDPOINTS.list, {
    method: 'GET',
    params,
  });
}

// 创建团队空间（仅限团队空间；user_id 为创建者）。
export function createSpace(body: { space_name: string }, params: SpaceUserIdParams) {
  return backendRequest<BackendApiEnvelope<SpaceDto>>(SPACE_ENDPOINTS.create, {
    method: 'POST',
    params,
    data: body,
  });
}

// 初始化个人空间（幂等：已存在则返回已有）。POST /openapi/v1/bots/spaces/personal/initialize?user_id=。
// 请求体默认为空（内部版契约不变）；阿里云部署形态由 Service 经 capability
// getPersonalSpaceInitOptions 注入 body（skipSC:true），Controller 不感知形态。
// 返回 SpaceDto（含 created 标识本次是否新建）。
export interface InitializePersonalSpaceBody {
  /** 阿里云部署形态后端要求的标志位（capability 按形态注入）；内部版不传。 */
  skipSC?: boolean;
}

export function initializePersonalSpace(params: SpaceUserIdParams, body?: InitializePersonalSpaceBody) {
  return backendRequest<BackendApiEnvelope<SpaceDto>>(SPACE_ENDPOINTS.personalInitialize, {
    method: 'POST',
    params,
    ...(body && Object.keys(body).length > 0 ? { data: body } : {}),
  });
}

// 删除团队空间（目标契约 §15-4；clawweb=Avernet 暂无此 DELETE 接口，UI 入口已隐藏，保留待后端补）。
export function deleteSpace(space_id: number | string) {
  return backendRequest<BackendApiEnvelope<{ deleted?: boolean }>>(SPACE_ENDPOINTS.detail(space_id), {
    method: 'DELETE',
  });
}

// 查询空间成员。
export function listSpaceMembers(space_id: number | string, params: SpaceMemberListParams) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<SpaceMemberDto>>>(SPACE_ENDPOINTS.members(space_id), {
    method: 'GET',
    params,
  });
}

// 添加普通成员（body.member_user_id 为被加成员；params.user_id 为操作者）。
export function addSpaceMember(space_id: number | string, body: AddSpaceMemberBody, params: SpaceUserIdParams) {
  return backendRequest<BackendApiEnvelope<SpaceMemberDto>>(SPACE_ENDPOINTS.members(space_id), {
    method: 'POST',
    params,
    data: body,
  });
}

// 删除普通成员（path member_user_id 为被删成员；params.user_id 为操作者）。
export function removeSpaceMember(space_id: number | string, member_user_id: string, params: SpaceUserIdParams) {
  return backendRequest<BackendApiEnvelope<{ deleted?: boolean }>>(SPACE_ENDPOINTS.member(space_id, member_user_id), {
    method: 'DELETE',
    params,
  });
}

// 修改成员角色（path member_user_id 为被改成员；params.user_id 为操作者）。
export function updateMemberRole(
  space_id: number | string,
  member_user_id: string,
  body: UpdateMemberRoleBody,
  params: SpaceUserIdParams,
) {
  return backendRequest<BackendApiEnvelope<SpaceMemberDto>>(SPACE_ENDPOINTS.memberRole(space_id, member_user_id), {
    method: 'PUT',
    params,
    data: body,
  });
}

// 申请加入空间（生成工单；body.reason 必填 min1）。
export function requestJoinSpace(space_id: number | string, body: RequestJoinBody, params: SpaceUserIdParams) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(SPACE_ENDPOINTS.joinRequests(space_id), {
    method: 'POST',
    params,
    data: body,
  });
}
