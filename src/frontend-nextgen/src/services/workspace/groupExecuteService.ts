/**
 * 自定义协作群 execute 建群 + 本地 BCS 群详情/会话 —— 从 groupService 物理拆出，
 * 以满足 Service≤300 行守卫。职责分离：
 * - 本文件：state_machine 自定义协作群经 task execute 建群，以及建群后通过预发 OpenAPI
 *   查询群详情/会话（loadBcsGroupDetail / loadGroupDetailOrBcs）。
 * - groupService：协作群通用 CRUD（loadGroups / loadGroupDetail / createGroup / update / dissolve）。
 *
 * 对外接口由 groupService 薄包装转发（同名方法），hook 调用方零改动。
 * 依赖注入规避循环依赖：回落 createGroup / 通用 loadGroupDetail 通过参数注入，
 * 本文件不 import groupService。
 *
 * 说明：GROUP_CREATE_VIA_EXECUTE 开关由 useCreateGroup 控制，仅 state_machine 自定义协作群
 * 进入 execute 链路；chat / manager_worker 仍走 groupService.createGroup。
 */
import type { GroupView, ParticipantRole } from '@/domain/collaboration';
import { executeTask } from '@/services/backendApi/tasks/taskController';
import type { Envelope, ExecuteTaskResponse } from '@/services/tasks/taskModel';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { CreateGroupInput } from './groupCreateRequest';
import { buildExecuteRequestFromGroup } from './groupTaskAdapter';
import type { DomainError, DomainResult } from './identityService';
import { mapBcsSessionItem, type BcsParticipantRaw, type BcsSessionRaw } from './mappers';

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

const ROLE_NATIVE_TO_DOMAIN: Record<string, ParticipantRole> = {
  driver: 'driver',
  manager: 'manager',
  consultant: 'member',
  worker: 'member',
  observer: 'member',
};

/** execute 建群后的群详情字段兼容类型，支持预发 OpenAPI 和历史 BCS raw 字段。 */
export interface BcsGroupDetailRaw {
  id?: string;
  group_id?: string;
  label?: string;
  name?: string;
  group_name?: string;
  group_strategy?: string;
  collaboration?: { strategy?: string };
  status?: string;
  participants?: BcsParticipantRaw[];
  updated_at?: number;
  created_at?: number;
  originator?: string;
  originator_actor_id?: string;
  driver_bot_owner?: string;
  membership?: 'direct' | 'session_only';
  visibility?: 'private' | 'public';
}

/**
 * execute 建群后的群详情：走预发 OpenAPI /openapi/v1/collaboration/groups/{id}，解析为 GroupView。
 *
 * 兼容历史 BCS raw 字段，同时接受 OpenAPI envelope.data，避免 execute 链路依赖本地服务。
 */
export async function loadBcsGroupDetail(groupId: string): Promise<DomainResult<GroupView>> {
  try {
    const base = '/openapi/v1/collaboration';
    const [dResp, sResp] = await Promise.all([
      fetch(`${base}/groups/${encodeURIComponent(groupId)}`, { credentials: 'include' }),
      fetch(`${base}/groups/${encodeURIComponent(groupId)}/sessions?offset=0&limit=50`, { credentials: 'include' }),
    ]);
    if (!dResp.ok) {
      return { ok: false, error: toDomainError('GROUP_MISSING', '该协作群不存在或已被删除。') };
    }
    const detailJson = await dResp.json();
    const g = (detailJson?.data ?? detailJson) as BcsGroupDetailRaw;
    const sessionJson = await sResp.json().catch(() => null);
    const sJson = (sessionJson?.data ?? sessionJson) as { items?: BcsSessionRaw[] } | BcsSessionRaw[] | null;
    const strategy: string = g.group_strategy ?? g.collaboration?.strategy ?? 'chat';
    const kind: GroupView['kind'] =
      strategy === 'manager_worker' ? 'task_master_slave' : strategy === 'state_machine' ? 'task_dag' : 'free_chat';
    const participants = (g.participants ?? []).map((p) => ({
      actorId: p.bot_uuid ?? p.actor_id ?? p.bot_id ?? '',
      kind: p.actor_kind ?? 'bot',
      name: p.bot_name ?? p.name ?? p.bot_uuid ?? p.actor_id ?? '',
      role: ROLE_NATIVE_TO_DOMAIN[p.role ?? 'worker'] ?? 'member',
      mode: p.mode ?? 'auto',
    }));
    const sessionItems = Array.isArray(sJson) ? sJson : sJson?.items ?? [];
    const sessions = sessionItems.map((s) => mapBcsSessionItem(s, g.id ?? g.group_id ?? groupId));
    const group: GroupView = {
      groupId: g.id ?? g.group_id ?? groupId,
      name: g.label ?? g.name ?? g.group_name ?? '未命名群',
      kind,
      status: g.status === 'dissolved' ? 'dissolved' : 'active',
      participants,
      sessions,
      lastMessageAt: g.updated_at ?? 0,
      createdAt: g.created_at ?? 0,
      participantCount: participants.length,
      ownerUserId: g.originator ?? g.originator_actor_id ?? g.driver_bot_owner ?? '',
      ...(g.membership ? { membership: g.membership } : {}),
      isPublic: g.visibility === 'public',
      deliveryPolicy: 'send_to_driver',
    };
    return { ok: true, data: group };
  } catch {
    return { ok: false, error: toDomainError('GROUP_LOAD_FAILED', '加载协作群失败，请稍后重试。') };
  }
}

/** 选中群详情统一入口：execute 建群后 markBcsGroup 的群走 OpenAPI 兼容映射，
 *  预发群走通用 loadGroupDetail（由调用方注入，避免循环依赖）。判断收敛于此。 */
export async function loadGroupDetailOrBcs(
  groupId: string,
  viewBotId: string | undefined,
  loadGroupDetail: (id: string, vid?: string) => Promise<DomainResult<GroupView>>,
): Promise<DomainResult<GroupView>> {
  return useWorkspaceStore.getState().bcsGroupIds[groupId]
    ? loadBcsGroupDetail(groupId)
    : loadGroupDetail(groupId, viewBotId);
}

/**
 * 走 task execute 创建自定义协作群(state_machine)。
 * - 非自定义协作群(非 state_machine) → 调用方注入的 fallbackCreateGroup 回落原链路。
 * - execute 以 Envelope{code,message,data,request_id} 统一封装：code=200000 为协议成功，
 *   其余 code（含 YAML 校验等业务错误）直接取 message 作 friendlyMessage 内联展示；
 *   HTTP 4xx/5xx/网络失败经 catch 映射。
 * - 建群成功依据 data.success && data.extend_props?.group_id（group_id 由后端 execute 同步回带），
 *   成功后标记 execute 群并经 loadBcsGroupDetail 拉回完整 GroupView。
 * - 开关见 useCreateGroup 的 GROUP_CREATE_VIA_EXECUTE；其有问题时关掉开关即回落原链路。
 */
export async function createGroupViaExecute(
  input: CreateGroupInput,
  ownerUserId: string,
  fallbackCreateGroup: (input: CreateGroupInput) => Promise<DomainResult<GroupView>>,
): Promise<DomainResult<GroupView>> {
  const req = buildExecuteRequestFromGroup(input, ownerUserId);
  if (!req) return fallbackCreateGroup(input);

  let resp: Envelope<ExecuteTaskResponse>;
  try {
    resp = await executeTask(req);
  } catch (err) {
    const status = (err as { status?: number })?.status;
    if (status === 400) {
      return { ok: false, error: toDomainError('GROUP_CREATE_INVALID', '创建参数校验不通过。') };
    }
    if (status === 403) {
      return { ok: false, error: toDomainError('GROUP_FORBIDDEN', '无权创建') };
    }
    return { ok: false, error: toDomainError('GROUP_CREATE_FAILED', '创建协作群失败，请稍后重试。') };
  }

  if (resp.code !== 200000) {
    return {
      ok: false,
      error: toDomainError('GROUP_CREATE_INVALID', resp.message || '建群任务执行失败，请稍后重试。'),
    };
  }
  const env = resp.data;
  if (!env || !env.success) {
    return {
      ok: false,
      error: toDomainError('GROUP_CREATE_FAILED', env?.message ?? '建群任务执行失败，请稍后重试。'),
    };
  }
  const groupId = env.extend_props?.group_id;
  if (!groupId) {
    return {
      ok: false,
      error: toDomainError('GROUP_CREATE_FAILED', '建群任务已提交，execute 尚未回带 group_id（后端待实现）。'),
    };
  }
  // 标记本地 BCS 群：此后该群的详情/会话查询（含选中后 useSelectedGroupDetail/useSessionMap 的自动补拉）自动走 BCS。
  useWorkspaceStore.getState().markBcsGroup(groupId);
  return loadBcsGroupDetail(groupId);
}
