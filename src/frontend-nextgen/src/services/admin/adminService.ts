// 管理后台 Service：空间管理用例（列表/搜索/创建/成员增删改/申请加入/权限判定）。
// 契约对齐 clawweb=Avernet：user_id query 由 Service 经 resolveUserId(activeIdentityId) 注入；
// 分页用 page_no；addMember body 用 member_user_id；requestJoin body 用 reason（必填）。
// 错误标准化（catch BackendRequestError -> {message,apiPath}），不 throw 到 Component。

import { mapSpaceDto, mapSpaceList, mapSpaceMemberDto, mapSpaceMemberList } from '@/domain/admin/mappers';
import type {
  CreateTeamSpaceInput,
  ServiceError,
  Space,
  SpaceListQuery,
  SpaceListResult,
  SpaceMember,
  SpaceMemberListQuery,
  SpaceMemberListResult,
} from '@/domain/admin/models';
import { ensureUserId, ensureUserName } from '@/services/admin/userIdentity';
import {
  addSpaceMember,
  createSpace,
  deleteSpace as deleteSpaceApi,
  initializePersonalSpace,
  listSpaceMembers,
  listSpaces,
  removeSpaceMember,
  requestJoinSpace,
  updateMemberRole,
} from '@/services/backendApi';
import type {
  SpaceListParams,
  SpaceMemberListParams,
  SpaceUserIdParams,
} from '@/services/backendApi/admin/spaceController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { isEnvelopeFailure, type BackendApiEnvelope } from '@/services/backendApi/types';
import { extractFriendlyErrorMessage, formatApiPath } from '@/utils/requestErrorHandler';

export interface AdminServiceOverview {
  module: string;
  description: string;
}

export interface SpaceServiceResult<T> {
  data?: T;
  error?: ServiceError;
}

// 从后端信封 body 直接读取可读 message（不沿用 extractErrorMessage：后者面向 error 形状，
// 会优先下钻 .data，对信封不适用）。
function envelopeMessage(data: unknown): string {
  if (typeof data === 'object' && data !== null) {
    const message = (data as BackendApiEnvelope<unknown>).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return '';
}

function envelopeRequestId(data: unknown): string | undefined {
  if (typeof data === 'object' && data !== null) {
    const requestId = (data as BackendApiEnvelope<unknown>).request_id;
    if (typeof requestId === 'string' && requestId.trim()) return requestId;
  }
  return undefined;
}

function toServiceError(e: unknown): ServiceError {
  if (e instanceof BackendRequestError) {
    // 后端 message 优先于已标准化的 e.message（后者经 extractFriendlyErrorMessage 取得）；
    // e.data 保留完整信封，message/request_id 从中取；无 body 时回退 e.message。
    return {
      message: envelopeMessage(e.data) || e.message,
      apiPath: e.apiPath,
      requestId: envelopeRequestId(e.data),
    };
  }
  return { message: extractFriendlyErrorMessage(e), apiPath: formatApiPath() };
}

// HTTP 2xx 但 code !== 200000 的业务失败:透传后端 message + request_id,
// 不再用操作类型预设文案(如「创建失败」)覆盖后端 message。
function envelopeToServiceError(env: BackendApiEnvelope<unknown>, apiPath: string): ServiceError {
  return {
    message: envelopeMessage(env) || '操作失败，请稍后重试',
    apiPath,
    requestId: envelopeRequestId(env),
  };
}

const MISSING_IDENTITY_ERROR: ServiceError = {
  message: '未获取到当前用户身份，请刷新后重试',
  apiPath: formatApiPath(),
};

function normalizeListParams(query: SpaceListQuery, user_id: string): SpaceListParams {
  const p: SpaceListParams = { user_id, page_no: query.page ?? 1, page_size: query.pageSize ?? 20 };
  if (query.keyword?.trim()) p.keyword = query.keyword.trim();
  if (query.spaceType && query.spaceType !== 'UNKNOWN') p.space_type = query.spaceType;
  if (query.scope) p.scope = query.scope;
  return p;
}

export const adminService = {
  getOverview(): AdminServiceOverview {
    return { module: 'admin', description: '管理后台 Service 承载空间管理用例并负责脱敏。' };
  },

  /** 空间列表（搜索/类型筛选/分页）。 */
  async listSpaces(query: SpaceListQuery = {}): Promise<SpaceServiceResult<SpaceListResult>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    try {
      const resp = await listSpaces(normalizeListParams(query, user_id));
      return { data: mapSpaceList(resp.data) };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 创建团队空间。 */
  async createTeamSpace(input: CreateTeamSpaceInput): Promise<SpaceServiceResult<Space>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    // 创建者花名：后端在创建空间时把创建者写入成员表，user_name 即其展示名（花名），
    // 传入后成员列表可直接展示花名而非工号。取不到或回落为工号时不传，避免把工号误写成花名。
    const user_name = await ensureUserName();
    try {
      const params: SpaceUserIdParams = { user_id };
      if (user_name && user_name !== user_id) params.user_name = user_name;
      const resp = await createSpace({ space_name: input.spaceName.trim() }, params);
      if (isEnvelopeFailure(resp)) return { error: envelopeToServiceError(resp, formatApiPath()) };
      if (!resp.data) return { error: { message: '创建失败：未返回空间数据', apiPath: formatApiPath() } };
      return { data: mapSpaceDto(resp.data).item };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 删除团队空间（仅 ADMIN；目标契约 §15-4，clawweb=Avernet 暂无此接口，UI 入口已隐藏）。 */
  async deleteSpace(spaceId: number | string): Promise<SpaceServiceResult<boolean>> {
    try {
      await deleteSpaceApi(spaceId);
      return { data: true };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 空间成员列表。 */
  async listMembers(
    spaceId: number | string,
    query: SpaceMemberListQuery = {},
  ): Promise<SpaceServiceResult<SpaceMemberListResult>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    try {
      const params: SpaceMemberListParams = {
        user_id,
        page_no: query.page ?? 1,
        page_size: query.pageSize ?? 20,
      };
      if (query.keyword?.trim()) params.keyword = query.keyword.trim();
      const resp = await listSpaceMembers(spaceId, params);
      return { data: mapSpaceMemberList(resp.data) };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 添加成员（被加成员 userId + 角色 + 可选花名；user_id 为操作者）。role 默认 MEMBER，可由调用方选管理员/成员。 */
  async addMember(
    spaceId: number | string,
    userId: string,
    role: 'ADMIN' | 'MEMBER' = 'MEMBER',
    userName?: string,
  ): Promise<SpaceServiceResult<SpaceMember>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    try {
      // 花名随被加成员一起写入成员表（body 字段 member_user_name 与 member_user_id 配对），避免成员列表只能展示工号。
      const body = { member_user_id: userId, role, ...(userName ? { member_user_name: userName } : {}) };
      const resp = await addSpaceMember(spaceId, body, { user_id });
      if (isEnvelopeFailure(resp)) return { error: envelopeToServiceError(resp, formatApiPath()) };
      if (!resp.data) return { error: { message: '添加失败：未返回成员数据', apiPath: formatApiPath() } };
      return { data: mapSpaceMemberDto(resp.data).item };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 删除成员（path 为被删成员 userId；user_id 为操作者）。 */
  async removeMember(spaceId: number | string, userId: string): Promise<SpaceServiceResult<boolean>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    try {
      await removeSpaceMember(spaceId, userId, { user_id });
      return { data: true };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 修改成员角色（path 为被改成员 userId；user_id 为操作者）。 */
  async updateRole(
    spaceId: number | string,
    userId: string,
    role: 'ADMIN' | 'MEMBER',
  ): Promise<SpaceServiceResult<SpaceMember>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    try {
      const resp = await updateMemberRole(spaceId, userId, { role }, { user_id });
      if (isEnvelopeFailure(resp)) return { error: envelopeToServiceError(resp, formatApiPath()) };
      if (!resp.data) return { error: { message: '修改失败：未返回成员数据', apiPath: formatApiPath() } };
      return { data: mapSpaceMemberDto(resp.data).item };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 申请加入空间（生成工单；reason 必填，后端 min1 max512）。 */
  async requestJoin(spaceId: number | string, reason: string): Promise<SpaceServiceResult<boolean>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    // 申请者花名：后端生成工单时记录申请人展示名（花名），传入后审批人可直接看到花名而非工号。
    // 取不到或回落为工号时不传，避免把工号误写成花名。
    const user_name = await ensureUserName();
    try {
      const params: SpaceUserIdParams = { user_id };
      if (user_name && user_name !== user_id) params.user_name = user_name;
      await requestJoinSpace(spaceId, { reason }, params);
      return { data: true };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /**
   * 确保存在个人空间（幂等）：listSpaces 后无 PERSONAL 时调 initialize，再 listSpaces 一次。
   * 调用方负责 localStorage 幂等标记，避免每次进入管理页都重复「查+建」往返。
   * 失败静默降级：initialize 失败不阻断初始化，回落到现有列表兜底。
   */
  async ensurePersonalSpace(): Promise<SpaceServiceResult<boolean>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    // 个人空间创建者=本人。后端把 owner 写入成员表，user_name 即其展示名（花名），
    // 传入后 owner 成员行可直接展示花名而非工号（同 createTeamSpace）；取不到或回落为工号时不传。
    const user_name = await ensureUserName();
    try {
      const params: SpaceUserIdParams = { user_id };
      if (user_name && user_name !== user_id) params.user_name = user_name;
      await initializePersonalSpace(params);
      return { data: true };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 权限判定：仅 ADMIN 可管理（加/删成员、改角色）。 */
  canManage(space: Space): { ok: boolean; reason?: string } {
    if (space.spaceType === 'PERSONAL') return { ok: false, reason: '个人空间不可管理成员' };
    if (space.currentUserRole !== 'ADMIN') return { ok: false, reason: '仅空间管理员可管理成员' };
    return { ok: true };
  },

  /**
   * 权限判定：仅「已加入」的空间可查看成员列表（打开成员抽屉）。
   * 已加入 = 当前用户在该空间是管理员/成员，或是空间创建者（owner 也算成员）。
   * 未加入时返回 ok:false，调用方（Hook）据此 toast 提示用户并阻止打开抽屉。
   */
  canViewMembers(space: Space): { ok: boolean; reason?: string } {
    const isMember = space.currentUserRole === 'ADMIN' || space.currentUserRole === 'MEMBER';
    if (isMember || space.isCreator === true) return { ok: true };
    return { ok: false, reason: '暂无权限查看该空间成员列表，加入空间后可查看' };
  },
};
