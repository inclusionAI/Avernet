import type {
  DeliveryPolicy,
  GroupKind,
  GroupSessionPage,
  GroupUpdatePatch,
  GroupView,
  IdentityView,
  ParticipantView,
} from '@/domain/collaboration';
import { getExtensionSlots } from '@/extensions/slots';
import {
  createGroup as createGroupApi,
  deleteGroup,
  getGroup,
  listGroups,
  updateGroup,
} from '@/services/backendApi/collaboration/collaborationGroupController';
import { listGroupSessions } from '@/services/backendApi/collaboration/sessionController';
import {
  loadBcsGroupDetail as execLoadBcsGroupDetail,
  loadGroupDetailOrBcs as execLoadGroupDetailOrBcs,
  loadGroupSessionsOrBcs as execLoadGroupSessionsOrBcs,
} from './groupBcsReadService';
import { buildCreateGroupBody, type CreateGroupInput } from './groupCreateRequest';
import type { DomainError, DomainResult } from './identityService';
import {
  isHumanMentionNotifyMode,
  mapGroupListItem,
  mapParticipant,
  mapSessionListItem,
  toParticipantSummary,
} from './mappers';

export interface PolicyResult {
  allowed: boolean;
  disabledReason?: string;
}

export interface VisibleGroupsOpts {
  search: string;
  kind: 'all' | GroupKind;
  sort: 'lastActivity' | 'createdAt';
}

export interface SessionPageOpts {
  offset?: number;
  limit?: number;
}

const GROUP_SESSION_PAGE_SIZE = 10;

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

function matchSearch(text: string, haystacks: string[]): boolean {
  const q = text.trim().toLowerCase();
  if (!q) return true;
  return haystacks.some((h) => h.toLowerCase().includes(q));
}

export const groupService = {
  async loadGroups(
    identity: IdentityView,
    opts: { q?: string; membership?: 'direct' | 'session_only' } = {},
  ): Promise<DomainResult<GroupView[]>> {
    try {
      const params: Record<string, unknown> = { offset: 0, limit: 50, kind: 'normal' };
      if (identity.kind === 'bot') params.view_bot_id = identity.id;
      if (opts.q?.trim()) params.q = opts.q.trim();
      if (opts.membership) params.membership = opts.membership;
      const resp = await listGroups(params as never);
      const items = (resp.data?.items ?? [])
        .filter((g) => g.kind === 'normal')
        .map((g) => mapGroupListItem(g));
      return { ok: true, data: items };
    } catch {
      return {
        ok: false,
        error: toDomainError('GROUPS_LOAD_FAILED', '加载协作群列表失败，请稍后重试。'),
      };
    }
  },

  /** 仅加载群的会话列表（/groups/{id}/sessions），不拉群详情。供侧栏选中/展开群填充会话；
   *  群详情（participants/owner/driver）仅在管理面板查看/编辑时按需拉取（见 loadGroupDetail）。 */
  async loadGroupSessions(
    groupId: string,
    viewBotId?: string,
    pageOpts: SessionPageOpts = {},
  ): Promise<DomainResult<GroupSessionPage>> {
    try {
      const offset = pageOpts.offset ?? 0;
      const limit = pageOpts.limit ?? GROUP_SESSION_PAGE_SIZE;
      const resp = await listGroupSessions(groupId, {
        offset,
        limit,
        ...(viewBotId ? { view_bot_id: viewBotId } : {}),
      });
      const items = (resp.data?.items ?? []).map((s) => mapSessionListItem(s));
      const total = resp.data?.total ?? offset + items.length;
      return {
        ok: true,
        data: {
          items,
          offset: resp.data?.offset ?? offset,
          limit: resp.data?.limit ?? limit,
          total,
          hasMore: offset + items.length < total,
        },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('GROUP_LOAD_FAILED', '加载会话列表失败，请稍后重试。'),
      };
    }
  },

  async loadGroupDetail(groupId: string, viewBotId?: string): Promise<DomainResult<GroupView>> {
    try {
      const [detail, sessions] = await Promise.all([
        getGroup(groupId),
        listGroupSessions(groupId, { offset: 0, limit: 50, ...(viewBotId ? { view_bot_id: viewBotId } : {}) }),
      ]);
      const d = detail.data;
      if (!d) {
        return {
          ok: false,
          error: toDomainError('GROUP_MISSING', '该协作群不存在或已被删除。'),
        };
      }
      const deliveryPolicy: DeliveryPolicy =
        d.collaboration.strategy === 'chat' ? d.collaboration.delivery_policy.bot_final_delivery : 'send_to_driver';
      const participants = d.participants.map(mapParticipant);
      const group: GroupView = {
        groupId: d.group_id,
        name: d.name ?? '未命名群',
        kind:
          d.collaboration.strategy === 'manager_worker'
            ? 'task_master_slave'
            : d.collaboration.strategy === 'state_machine'
            ? 'task_dag'
            : 'free_chat',
        status: d.status === 'dissolved' ? 'dissolved' : 'active',
        participants,
        participantSummaries: participants.map(toParticipantSummary),
        participantDetailsLoaded: true,
        sessions: (sessions.data?.items ?? []).map((s) => mapSessionListItem(s)),
        lastMessageAt: d.updated_at,
        createdAt: d.created_at,
        participantCount: d.participants.length,
        ownerUserId: d.originator_actor_id,
        driverBotUuid: d.driver_bot_uuid,
        ...(d.initial_session_id ? { initialSessionId: d.initial_session_id } : {}),
        ...(d.membership ? { membership: d.membership } : {}),
        ...(isHumanMentionNotifyMode(d.human_mention_notify_mode)
          ? { humanMentionNotifyMode: d.human_mention_notify_mode }
          : {}),
        isPublic: d.visibility === 'public',
        deliveryPolicy,
      };
      return { ok: true, data: group };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 404) {
        return {
          ok: false,
          error: toDomainError('GROUP_MISSING', '该协作群不存在或已被删除。'),
        };
      }
      return {
        ok: false,
        error: toDomainError('GROUP_LOAD_FAILED', '加载协作群失败，请稍后重试。'),
      };
    }
  },

  /** 选中群详情统一入口：本地 BCS 群(execute 建群) 走 loadBcsGroupDetail，预发群走 loadGroupDetail。
   *  实现下沉 groupBcsReadService（依赖注入通用 loadGroupDetail 规避循环依赖）。供 hook 选中补拉调用。 */
  loadGroupDetailOrBcs(groupId: string, viewBotId?: string): Promise<DomainResult<GroupView>> {
    return execLoadGroupDetailOrBcs(groupId, viewBotId, (id, vid) => this.loadGroupDetail(id, vid));
  },

  /** 侧栏群行头像堆成员补拉（2026-10-12 群行去文件夹、还原设计稿）：群列表接口
   *  （BCS NormalGroupSummary）只回传 participant_count、不含 participants 明细，
   *  群行渲染真实头像需按群拉一次详情；这里仅透出 participants，其他字段不消费。
   *  拉取失败或详情无成员返回 null（不抛错），缓存与并发预算由调用方（useGroupWorkspace）负责。 */
  async loadGroupParticipants(groupId: string, viewBotId?: string): Promise<ParticipantView[] | null> {
    const res = await this.loadGroupDetailOrBcs(groupId, viewBotId);
    return res.ok && res.data.participants.length > 0 ? res.data.participants : null;
  },

  /** 选中/展开群填充会话列表的统一入口（仅会话，不含群详情）：BCS 群走 loadBcsGroupSessions，
   *  预发群走 loadGroupSessions。供 useSessionMap 调用，避免选中群触发 GET /groups/{id} 详情请求。 */
  loadGroupSessionsOrBcs(
    groupId: string,
    viewBotId?: string,
    pageOpts?: SessionPageOpts,
  ): Promise<DomainResult<GroupSessionPage>> {
    return execLoadGroupSessionsOrBcs(groupId, viewBotId, pageOpts, (id, vid, opts) =>
      this.loadGroupSessions(id, vid, opts),
    );
  },

  /** 本地 BCS 群详情（execute 建群返回的群）：实现下沉 groupBcsReadService。 */
  loadBcsGroupDetail(groupId: string): Promise<DomainResult<GroupView>> {
    return execLoadBcsGroupDetail(groupId);
  },

  getVisibleGroups(groups: GroupView[], opts: VisibleGroupsOpts): GroupView[] {
    const filtered = groups
      .filter((g) => opts.kind === 'all' || g.kind === opts.kind)
      .filter((g) => matchSearch(opts.search, [g.name, g.groupId]));
    const key = opts.sort === 'lastActivity' ? ('lastMessageAt' as const) : ('createdAt' as const);
    return [...filtered].sort((a, b) => b[key] - a[key]);
  },

  canManageGroup(group: GroupView | null, identityId: string | null): PolicyResult {
    if (!group || !identityId) return { allowed: false, disabledReason: '未选择协作群' };
    const isOwner = group.ownerUserId === identityId;
    const isDriverOrManager = group.participants.some(
      (p) => p.actorId === identityId && (p.role === 'driver' || p.role === 'manager'),
    );
    const isDriverBot = group.driverBotUuid === identityId;
    if (isOwner || isDriverOrManager || isDriverBot) return { allowed: true };
    return { allowed: false, disabledReason: '仅群主/主节点可管理该协作群' };
  },

  canDissolveGroup(group: GroupView | null, identityId: string | null): PolicyResult {
    if (group?.status === 'dissolved') {
      return { allowed: false, disabledReason: '该协作群已解散' };
    }
    return this.canManageGroup(group, identityId);
  },

  /**
   * 创建协作群。strategy→collaboration 映射：
   * - chat → { strategy:'chat', delivery_policy:{bot_final_delivery:deliveryPolicy??'send_to_driver'} }
   * - manager_worker → { strategy:'manager_worker' }
   * - state_machine → { strategy:'state_machine', definition:{ content_yaml:definitionYaml }, participant_bindings:[{binding:'role-1', actor_ids:[...participants.actor_id]}] }（definition 与 participant_bindings 为同级，对应后端 StateMachineConfiguration）
   *
   * 错误映射：400→保留后端 friendlyMessage（YAML 校验等业务错误，inline 展示）；409→GROUP_CONFLICT；
   * 403→「无权创建」；其余 generic。成功后通过 loadGroupDetail 拉回最新 GroupView。
   */
  async createGroup(input: CreateGroupInput): Promise<DomainResult<GroupView>> {
    const body = buildCreateGroupBody(input);
    try {
      const resp = await createGroupApi(body);
      const created = resp.data;
      const groupId = created?.group_id;
      if (!created || !groupId) {
        return {
          ok: false,
          error: toDomainError('GROUP_CREATE_FAILED', '创建协作群失败，请稍后重试。'),
        };
      }
      const loaded = await this.loadGroupDetail(groupId);
      if (!loaded.ok) return loaded;
      return {
        ok: true,
        data: {
          ...loaded.data,
          ...(created.initial_session_id ? { initialSessionId: created.initial_session_id } : {}),
          ...(created.initial_run
            ? {
                initialRun: {
                  runId: created.initial_run.run_id,
                  botUuid: created.initial_run.bot_uuid,
                  activityKind: created.initial_run.activity_kind,
                  state: created.initial_run.state,
                  startedAt: created.initial_run.started_at,
                },
              }
            : {}),
        },
      };
    } catch (err) {
      const e = err as { status?: number; message?: string };
      const status = e?.status;
      if (status === 400) {
        return {
          ok: false,
          error: toDomainError('GROUP_CREATE_INVALID', e?.message ?? '创建参数校验不通过。'),
        };
      }
      if (status === 403) {
        return { ok: false, error: toDomainError('GROUP_FORBIDDEN', '无权创建') };
      }
      if (status === 409) {
        return {
          ok: false,
          error: toDomainError('GROUP_CONFLICT', '协作群状态已变更，请刷新后重试。'),
        };
      }
      return {
        ok: false,
        error: toDomainError('GROUP_CREATE_FAILED', '创建协作群失败，请稍后重试。'),
      };
    }
  },

  /** 走 task execute 创建自定义协作群(state_machine)：经扩展槽位 groupTasks.createGroupViaExecute 委派
   *  真实执行器（实现 groupExecuteService.createGroupViaExecute，装配见 src/extensions/empty.ts；
   *  回落 createGroup 经参数注入，规避循环依赖）。槽位 null（collab 形态）下不静默改走普通建群、
   *  不触任务引擎，而是返回 GROUP_EXECUTE_UNAVAILABLE。开关见 useCreateGroup 的
   *  GROUP_CREATE_VIA_EXECUTE；开关 false 时不被触发，自定义协作群走 createGroup 默认链路。 */
  createGroupViaExecute(input: CreateGroupInput, ownerUserId: string): Promise<DomainResult<GroupView>> {
    const execute = getExtensionSlots().groupTasks?.createGroupViaExecute;
    if (!execute) {
      return Promise.resolve({
        ok: false,
        error: toDomainError('GROUP_EXECUTE_UNAVAILABLE', '当前站点不支持以任务执行方式创建协作群。'),
      });
    }
    return execute(input, ownerUserId, (value) => this.createGroup(value));
  },

  async updateGroup(
    groupId: string,
    patch: GroupUpdatePatch,
  ): Promise<DomainResult<GroupView>> {
    try {
      await updateGroup(groupId, {
        name: patch.name,
        visibility: patch.visibility,
        delivery_policy: patch.deliveryPolicy ? { bot_final_delivery: patch.deliveryPolicy } : undefined,
        human_mention_notify_mode: patch.humanMentionNotifyMode,
      });
      return this.loadGroupDetail(groupId);
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 409) {
        return {
          ok: false,
          error: toDomainError('GROUP_CONFLICT', '协作群状态已变更，请刷新后重试。'),
        };
      }
      return {
        ok: false,
        error: toDomainError('GROUP_UPDATE_FAILED', '更新协作群失败，请稍后重试。'),
      };
    }
  },

  async dissolveGroup(groupId: string): Promise<DomainResult<null>> {
    try {
      await deleteGroup(groupId);
      return { ok: true, data: null };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 409) {
        return {
          ok: false,
          error: toDomainError('GROUP_CONFLICT', '协作群状态已变更，请刷新后重试。'),
        };
      }
      return {
        ok: false,
        error: toDomainError('GROUP_DISSOLVE_FAILED', '解散协作群失败，请稍后重试。'),
      };
    }
  },
};
