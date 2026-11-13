/**
 * 本地 BCS 群(execute 建群标记 / bcs_grp_ 前缀)的详情/会话只读链路 —— 自 groupExecuteService
 * 物理拆出（2026-11 collab-form Task 7A）：task execute 建群器与群只读链路解耦，使 groupService
 * 等建群消费方不再经只读链路静态触达任务引擎代码。职责：
 * - 本文件：BCS 群经预发 OpenAPI 查询详情/会话（loadBcsGroupDetail / loadBcsGroupSessions），
 *   含 raw-fetch 旁路的 ACE 登录体登记、历史 BCS raw 字段兼容，以及选中/展开群的统一入口
 *   （loadGroupDetailOrBcs / loadGroupSessionsOrBcs）与并发会话请求去重。
 * - groupExecuteService：仅保留 execute 建群器（createGroupViaExecute），建群成功后的详情
 *   回读改由本模块的 loadBcsGroupDetail 提供。
 *
 * 模块图不变量（见 src/extensions/slots.ts 注释）：本模块禁止 import executeTask /
 * groupTaskAdapter / groupExecuteService —— 任务引擎依赖收敛在 groupExecuteService，
 * 经扩展槽位 groupTasks.createGroupViaExecute 消费（装配见 src/extensions/empty.ts）。
 * 依赖注入规避循环依赖：通用 loadGroupDetail / loadGroupSessions 通过参数注入，
 * 本文件仅对 groupService 保持 type-only import。
 */
import type { GroupSessionPage, GroupView, ParticipantRole, ParticipantView } from '@/domain/collaboration';
import { extractLoginUrl, isAceLoginResponse } from '@/services/backendApi/aceLoginBody';
import { triggerAceLoginRedirect } from '@/services/backendApi/httpClient';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { SessionPageOpts } from './groupService';
import type { DomainError, DomainResult } from './identityService';
import { normalizeAvatarUrl } from '@/utils/avatarSource';
import {
  isHumanMentionNotifyMode,
  mapBcsSessionItem,
  toParticipantSummary,
  type BcsParticipantRaw,
  type BcsSessionRaw,
} from './mappers';

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

function safeString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

const ROLE_NATIVE_TO_DOMAIN: Record<string, ParticipantRole> = {
  driver: 'driver',
  manager: 'manager',
  consultant: 'member',
  worker: 'worker',
  observer: 'member',
};

const groupSessionRequestsInFlight = new Map<string, Promise<DomainResult<GroupSessionPage>>>();

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
  /** @Human 消息外部渠道通知来源；历史 BCS raw 详情可能缺省。 */
  human_mention_notify_mode?: 'all' | 'driver_bot_only' | 'none';
}

/**
 * BCS 群会话列表（sessions-only）：仅 GET /groups/{id}/sessions，不拉群详情。
 * 供 useSessionMap 选中/展开群填充会话——选中群不再触发 GET /groups/{id} 详情请求，
 * 群详情仅在管理面板（查看/编辑）时按需拉取（见 loadBcsGroupDetail）。兼容历史 BCS raw 字段。
 * view_bot_id 语义与通用端点一致：当前身份 mine bot_id（bot_id 原始值，含 human_ 前缀形态），
 * 供后端按当前角色视角过滤会话；缺省（无身份）时省略参数。
 */
export async function loadBcsGroupSessions(
  groupId: string,
  pageOpts: SessionPageOpts = {},
  viewBotId?: string,
): Promise<DomainResult<GroupSessionPage>> {
  try {
    const base = '/openapi/v1/collaboration';
    const offset = pageOpts.offset ?? 0;
    const limit = pageOpts.limit ?? 10;
    const viewParam = viewBotId ? `&view_bot_id=${encodeURIComponent(viewBotId)}` : '';
    const sResp = await fetch(
      `${base}/groups/${encodeURIComponent(groupId)}/sessions?offset=${offset}&limit=${limit}${viewParam}`,
      {
        credentials: 'include',
      },
    );
    const sessionJson = await sResp.json().catch(() => null);
    // raw-fetch 旁路打团队网关:未登录时网关放回 ACE 登录体(绕过 httpClient),登记单飞跳转后返回空列表。
    if (isAceLoginResponse(sessionJson)) {
      const loginUrl = extractLoginUrl(sessionJson);
      triggerAceLoginRedirect(loginUrl);
      return {
        ok: true,
        data: { items: [], offset, limit, total: 0, hasMore: false },
      };
    }
    const sJson = (sessionJson?.data ?? sessionJson) as
      | { items?: BcsSessionRaw[]; offset?: number; limit?: number; total?: number; hasMore?: boolean }
      | BcsSessionRaw[]
      | null;
    const sessionItems = Array.isArray(sJson) ? sJson : sJson?.items ?? [];
    const items = sessionItems.map((s) => mapBcsSessionItem(s, groupId));
    const total = Array.isArray(sJson) ? offset + items.length : sJson?.total ?? offset + items.length;
    return {
      ok: true,
      data: {
        items,
        offset: Array.isArray(sJson) ? offset : sJson?.offset ?? offset,
        limit: Array.isArray(sJson) ? limit : sJson?.limit ?? limit,
        total,
        hasMore: Array.isArray(sJson) ? items.length >= limit : sJson?.hasMore ?? offset + items.length < total,
      },
    };
  } catch {
    return { ok: false, error: toDomainError('GROUP_LOAD_FAILED', '加载会话列表失败，请稍后重试。') };
  }
}

/**
 * execute 建群后的群详情：走预发 OpenAPI /openapi/v1/collaboration/groups/{id}，解析为 GroupView。
 *
 * 兼容历史 BCS raw 字段，同时接受 OpenAPI envelope.data，避免 execute 链路依赖本地服务。
 */
export async function loadBcsGroupDetail(groupId: string, viewBotId?: string): Promise<DomainResult<GroupView>> {
  try {
    const base = '/openapi/v1/collaboration';
    const viewParam = viewBotId ? `&view_bot_id=${encodeURIComponent(viewBotId)}` : '';
    const [dResp, sResp] = await Promise.all([
      fetch(`${base}/groups/${encodeURIComponent(groupId)}`, { credentials: 'include' }),
      fetch(`${base}/groups/${encodeURIComponent(groupId)}/sessions?offset=0&limit=50${viewParam}`, {
        credentials: 'include',
      }),
    ]);
    if (!dResp.ok) {
      return { ok: false, error: toDomainError('GROUP_MISSING', '该协作群不存在或已被删除。') };
    }
    const detailJson = await dResp.json();
    // raw-fetch 旁路:群详情未登录时网关放回 ACE 登录体,登记单飞跳转后返回失败(跳转在即)。
    if (isAceLoginResponse(detailJson)) {
      const loginUrl = extractLoginUrl(detailJson);
      triggerAceLoginRedirect(loginUrl);
      return { ok: false, error: toDomainError('GROUP_LOAD_FAILED', '登录状态已失效，请重新登录后重试') };
    }
    const g = (detailJson?.data ?? detailJson) as BcsGroupDetailRaw;
    const sessionJson = await sResp.json().catch(() => null);
    const sJson = (sessionJson?.data ?? sessionJson) as { items?: BcsSessionRaw[] } | BcsSessionRaw[] | null;
    const strategy = safeString(g.group_strategy, safeString(g.collaboration?.strategy, 'chat'));
    const kind: GroupView['kind'] =
      strategy === 'manager_worker' ? 'task_master_slave' : strategy === 'state_machine' ? 'task_dag' : 'free_chat';
    const participants: ParticipantView[] = (Array.isArray(g.participants) ? g.participants : []).map((p) => ({
      actorId: safeString(p.bot_uuid, safeString(p.actor_id, safeString(p.bot_id))),
      kind: p.actor_kind === 'human' ? 'human' : 'bot',
      name: safeString(p.bot_name, safeString(p.name, safeString(p.bot_uuid, safeString(p.actor_id)))),
      avatarUrl: normalizeAvatarUrl(p.avatar_url),
      role: ROLE_NATIVE_TO_DOMAIN[safeString(p.role, 'worker')] ?? 'member',
      mode: p.mode === 'muted' || p.mode === 'absent' || p.mode === 'present' ? p.mode : 'auto',
    }));
    const sessionItems = Array.isArray(sJson) ? sJson : sJson?.items ?? [];
    const resolvedGroupId = safeString(g.id, safeString(g.group_id, groupId));
    const sessions = sessionItems.map((s) => mapBcsSessionItem(s, resolvedGroupId));
    const group: GroupView = {
      groupId: resolvedGroupId,
      name: safeString(g.label, safeString(g.name, safeString(g.group_name, '未命名群'))),
      kind,
      status: g.status === 'dissolved' ? 'dissolved' : 'active',
      participants,
      participantSummaries: participants.map(toParticipantSummary),
      participantDetailsLoaded: true,
      sessions,
      lastMessageAt: typeof g.updated_at === 'number' ? g.updated_at : 0,
      createdAt: typeof g.created_at === 'number' ? g.created_at : 0,
      participantCount: participants.length,
      ownerUserId: safeString(g.originator, safeString(g.originator_actor_id, safeString(g.driver_bot_owner))),
      ...(g.membership ? { membership: g.membership } : {}),
      ...(isHumanMentionNotifyMode(g.human_mention_notify_mode)
        ? { humanMentionNotifyMode: g.human_mention_notify_mode }
        : {}),
      isPublic: g.visibility === 'public',
      deliveryPolicy: 'send_to_driver',
    };
    return { ok: true, data: group };
  } catch {
    return { ok: false, error: toDomainError('GROUP_LOAD_FAILED', '加载协作群失败，请稍后重试。') };
  }
}

/** 是否为 BCS(execute) 群:内存标记(会话内建群时 markBcsGroup 写入)命中,或 groupId 按 `bcs_grp_` 前缀约定命中。
 *  新开页 `target=_blank` 全新挂载时 `bcsGroupIds` 内存态为空(非持久化),仅靠标记会把既有 BCS 群误判为预发群。
 *  前缀兜底保证 BCS 群始终走 BCS raw-fetch 旁路(ACE 登录体处理 + 历史字段兼容),与 ViewSessionButton /
 *  NodeListView / taskPanelMapper 判定单/群的口径(`bcs_grp_` 前缀)对齐(后端建群 group_id 即 `bcs_grp_<uuid>`)。
 *  view_bot_id 两条路径语义一致:当前身份 mine bot_id,缺省时省略。 */
function isBcsGroupId(groupId: string): boolean {
  return Boolean(useWorkspaceStore.getState().bcsGroupIds[groupId]) || groupId.startsWith('bcs_grp_');
}

/** 选中群详情统一入口：execute 建群后 markBcsGroup 的群走 OpenAPI 兼容映射，
 *  预发群走通用 loadGroupDetail（由调用方注入，避免循环依赖）。判断收敛于此。 */
export async function loadGroupDetailOrBcs(
  groupId: string,
  viewBotId: string | undefined,
  loadGroupDetail: (id: string, vid?: string) => Promise<DomainResult<GroupView>>,
): Promise<DomainResult<GroupView>> {
  return isBcsGroupId(groupId) ? loadBcsGroupDetail(groupId, viewBotId) : loadGroupDetail(groupId, viewBotId);
}

/** 选中/展开群填充会话列表的统一入口：BCS 群走 loadBcsGroupSessions，
 *  预发群走通用 loadGroupSessions（由调用方注入，避免循环依赖）。仅调 /groups/{id}/sessions，
 *  不调 /groups/{id} 详情（详情仅管理面板按需拉，见 loadGroupDetailOrBcs）。判断收敛于此。 */
export async function loadGroupSessionsOrBcs(
  groupId: string,
  viewBotId: string | undefined,
  pageOpts: SessionPageOpts | undefined,
  loadGroupSessions: (id: string, vid?: string, opts?: SessionPageOpts) => Promise<DomainResult<GroupSessionPage>>,
): Promise<DomainResult<GroupSessionPage>> {
  const isBcsGroup = isBcsGroupId(groupId);
  const requestKey = JSON.stringify([isBcsGroup, groupId, viewBotId ?? null, pageOpts ?? {}]);
  const existingRequest = groupSessionRequestsInFlight.get(requestKey);
  if (existingRequest) return existingRequest;
  const request = (
    isBcsGroup ? loadBcsGroupSessions(groupId, pageOpts, viewBotId) : loadGroupSessions(groupId, viewBotId, pageOpts)
  ).finally(() => {
    if (groupSessionRequestsInFlight.get(requestKey) === request) {
      groupSessionRequestsInFlight.delete(requestKey);
    }
  });
  groupSessionRequestsInFlight.set(requestKey, request);
  return request;
}
