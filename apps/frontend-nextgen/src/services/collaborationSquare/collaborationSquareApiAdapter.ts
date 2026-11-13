import { getCapabilities } from '@/capabilities';
import type { MessageViewScope } from '@/domain/collaboration/types';
import { mapBcsBotCandidate } from '@/domain/collaborationSquare/bcsBotMapper';
import {
  mapBotProfileTransport,
  mapPublicBotCatalogSummaryToProfile,
  mapPublicGroupCatalogDto,
} from '@/domain/collaborationSquare/mapper';
import { mapBbsTaskItemDto, mapPlazaStatusToBbsStatus } from '@/domain/collaborationSquare/taskMapper';
import type {
  BotCandidatePage,
  CollaborationSquarePage,
  CreateSessionResult,
  FriendRequestActor,
  FriendRequestResult,
  HumanBotActionContext,
  OpenBotConversationOptions,
  OpenBotConversationResult,
  PublicBot,
  PublicBotDiscoveryQuery,
  PublicBotDiscoveryResult,
  PublicBotProfile,
  PublicBotSearchQuery,
  PublicGroup,
  PublicGroupMember,
  PublicGroupMembersDetail,
  PublicGroupSearchQuery,
  PublicTask,
  PublicTaskPage,
  PublicTaskSearchQuery,
} from '@/domain/collaborationSquare/types';
import { getPublicBotTargetId } from '@/domain/collaborationSquare/types';
import { createBotSession } from '@/services/backendApi/bots/privateBotSessionController';
import { BOT_ENDPOINTS, listBotMetadata, type BotMetadataDto, type BotMetadataQueryItem } from '@/services/backendApi/bots/botController';
import { listBbsTasks } from '@/services/backendApi/collaboration/bbsTaskController';
import { createFriendConnectionRequest } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { getGroup, listPublicGroups } from '@/services/backendApi/collaboration/collaborationGroupController';
import { getPublicBotProfile, PublicBotCatalogError } from '@/services/backendApi/collaboration/publicBotController';
import { createSession as createGroupSessionRequest } from '@/services/backendApi/collaboration/sessionController';
import { isEnvelopeFailure } from '@/services/backendApi/types';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { listBcsBotCandidates, resolveHumanBcsActorId, searchBcsCandidates } from '@/services/bcs/botCandidatesController';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import {
  isAceLoginRedirectError,
  isAceLoginResponse,
  mapActionError,
  mapListError,
  mapProfileError,
  mapSessionRole,
  resolveBotNames,
  splitBotId,
  unsupported,
} from './collaborationSquareApiHelpers';
import { CollaborationSquareError } from './collaborationSquareError';
import type { CollaborationSquareGateway } from './collaborationSquareGateway';
import { listFriendBotRelationships, listPendingFriendBotRelationships } from './friendConnectionRelationships';
import { filterPublicBotsByMetadata } from './publicBotMetadata';
import { readPublicBotPage } from './publicBotCursor';

async function enrichRelationships(bots: PublicBot[], actor?: FriendRequestActor, signal?: AbortSignal) {
  if (!actor || bots.length === 0) return bots;
  const { friendIds, applyingIds } = await listFriendBotRelationships(actor, signal);
  return bots.map((bot) => ({
    ...bot,
    relationshipStatus: friendIds.has(getPublicBotTargetId(bot))
      ? ('friend' as const)
      : applyingIds.has(getPublicBotTargetId(bot))
      ? ('applying' as const)
      : bot.relationshipStatus,
  }));
}

async function enrichPendingRelationships(bots: PublicBot[], actor?: FriendRequestActor, signal?: AbortSignal) {
  if (!actor || bots.length === 0) return bots;
  const applyingIds = await listPendingFriendBotRelationships(actor, signal);
  return bots.map((bot) => ({
    ...bot,
    relationshipStatus:
      bot.isOwnedByLoggedInUser || bot.relationshipStatus === 'friend'
        ? bot.relationshipStatus
        : applyingIds.has(getPublicBotTargetId(bot))
        ? ('applying' as const)
        : bot.relationshipStatus,
  }));
}

/** 目标 Bot 所有者判断始终使用登录用户；viewer 只决定关系与可见范围。 */
function resolveLoggedInUserId(
  query: { viewerActorType?: 'human' | 'bot'; viewerActorId?: string },
  context?: HumanBotActionContext,
): string | undefined {
  return context?.userId ?? (query.viewerActorType === 'human' ? query.viewerActorId : undefined);
}

function resolveEnrichActor(
  query: { viewerActorType?: 'human' | 'bot'; viewerActorId?: string },
  context?: HumanBotActionContext,
): FriendRequestActor | undefined {
  if (query.viewerActorType && query.viewerActorId) {
    return { type: query.viewerActorType, id: query.viewerActorId };
  }
  return context ? { type: 'human', id: context.userId } : undefined;
}

/** Backend 可选增强的启用决策：部署能力开启且存在可用的登录 Human 身份（metadata 接口校验请求者）。 */
function isPublicBotBackendEnhancementEnabled(userId: string): boolean {
  return getCapabilities().getPublicBotBackendEnabled() && Boolean(userId.trim());
}

const ENHANCEMENT_UNAVAILABLE_TOAST_ID = 'collaboration-square-bot-metadata-unavailable';

/**
 * metadata 增强不可用（网络/5xx）的一次性用户提示：BCS 结果已保留，只提示增强降级，不重复叠加。
 * Service 层守「只 enqueue、不直调 toast」：由顶层 useErrorNotifyObserver 按稳定 id=toastKey
 * 统一投递并去重合并（文案与既有版本完全一致）。
 */
function notifyBotEnhancementUnavailable(): void {
  useErrorNotifyStore.getState().enqueue({
    toastKey: ENHANCEMENT_UNAVAILABLE_TOAST_ID,
    message: 'Bot 展示增强暂不可用，已展示基础信息',
  });
}

/**
 * metadata/queries 封装（FetchPublicBotMetadata 契约）：user_id 显式根登录 Human（不可用所选
 * Bot 身份代替），page_size 覆盖整批身份对；信封失败按传输错误上抛（认证类错误由
 * filterPublicBotsByMetadata 归类为鉴权路径原样传播）。
 */
function fetchPublicBotsMetadata(userId: string, pairs: BotMetadataQueryItem[], signal?: AbortSignal): Promise<BotMetadataDto[]> {
  const trimmedUserId = userId.trim();
  if (!trimmedUserId || pairs.length === 0) return Promise.resolve([]);
  return listBotMetadata({ user_id: trimmedUserId, page_size: pairs.length }, { bots: pairs }, signal).then((response) => {
    if (isEnvelopeFailure(response)) {
      throw new BackendRequestError('Bot metadata 响应不是成功信封', {
        apiPath: BOT_ENDPOINTS.metadataQueries,
        data: response,
      });
    }
    const items = response.data?.items;
    if (!Array.isArray(items)) {
      throw new BackendRequestError('Bot metadata 响应结构无效', {
        apiPath: BOT_ENDPOINTS.metadataQueries,
        data: response,
      });
    }
    return items;
  });
}

/** BCS candidates 的视角 actor：Bot 视角用所选完整身份，其余从登录 Human user_id 生成 human_<id>。 */
function resolveBcsCandidateActorId(
  query: { viewerActorType?: 'human' | 'bot'; viewerActorId?: string },
  context?: HumanBotActionContext,
): string {
  if (query.viewerActorType === 'bot') {
    const botActor = query.viewerActorId?.trim();
    if (!botActor) throw new CollaborationSquareError('protocol_error', '当前 Bot 身份不可用，无法查询公开 Bot');
    return botActor;
  }
  const explicitUserId = query.viewerActorType === 'human' ? query.viewerActorId : undefined;
  const userId = (context?.userId ?? explicitUserId ?? '').trim();
  if (!userId) throw new CollaborationSquareError('protocol_error', '缺少登录用户身份，无法查询公开 Bot');
  return resolveHumanBcsActorId(userId);
}

function isAbortLike(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as { name?: unknown }).name === 'AbortError';
}

/**
 * Backend 画像增强失败后的兜底策略：鉴权类失败沿既有路径上抛（mapProfileError 已取消协议层
 * 默认提示），其余失败（404 不在公开目录、5xx、协议异常）回退 BCS 摘要——申请弹窗保持可用。
 */
function fallbackBotProfile(fallback: PublicBotProfile, error: unknown): PublicBotProfile {
  if (isAbortLike(error)) throw error;
  if (
    isAceLoginRedirectError(error) ||
    (error instanceof BackendRequestError && (error.status === 401 || error.status === 403)) ||
    (error instanceof PublicBotCatalogError && error.code === 'unauthenticated')
  ) {
    return mapProfileError(error);
  }
  if (error instanceof BackendRequestError && error.toastKey) useErrorNotifyStore.getState().cancel(error.toastKey);
  return fallback;
}

/**
 * 接入 BCS v1 公开候选与语义搜索、可选 Backend 画像/metadata 增强、Human→Bot 关系/会话与
 * 公开群列表；扫描循环在 publicBotCursor，群写保持显式 unsupported。
 */
export class CollaborationSquareApiAdapter implements CollaborationSquareGateway {
  async listBotPage(
    query: PublicBotSearchQuery = {},
    context?: HumanBotActionContext,
    signal?: AbortSignal,
  ): Promise<BotCandidatePage> {
    try {
      const name = query.search?.trim() || undefined;
      const actorId = resolveBcsCandidateActorId(query, context);
      const userId = resolveLoggedInUserId(query, context) ?? '';
      const page = await readPublicBotPage({
        actorId,
        userId,
        offset: query.offset ?? 0,
        pageSize: query.pageSize ?? 20,
        backendEnabled: isPublicBotBackendEnhancementEnabled(userId),
        fetchCandidates: (actor, pageQuery, fetchSignal) =>
          listBcsBotCandidates(actor, { name, offset: pageQuery.offset, limit: pageQuery.limit }, fetchSignal).then(
            (envelope) => envelope.data,
          ),
        fetchMetadata: fetchPublicBotsMetadata,
        signal,
        onEnhancementUnavailable: notifyBotEnhancementUnavailable,
      });
      // BCS 列表已返回 is_friend；仅补读 sent/pending 申请，保留刷新后的“申请中”状态。
      // 回填 actor 跟随 viewer（对话协作当前角色 tab），缺省为登录人类（协作广场）。
      return { ...page, items: await enrichPendingRelationships(page.items, resolveEnrichActor(query, context), signal) };
    } catch (error) {
      return mapListError(error, 'Bot');
    }
  }

  async listBots(
    query: PublicBotSearchQuery = {},
    context?: HumanBotActionContext,
    signal?: AbortSignal,
  ): Promise<PublicBot[]> {
    return (await this.listBotPage(query, context, signal)).items;
  }

  async discoverBots(
    query: PublicBotDiscoveryQuery,
    context?: HumanBotActionContext,
    signal?: AbortSignal,
  ): Promise<PublicBotDiscoveryResult> {
    try {
      const keyword = query.keyword.trim();
      // 空关键词按契约 empty_query 语义本地短路：不发 BCS 搜索、metadata 或关系回填请求。
      if (!keyword) return { items: [], searchMode: 'empty_query' };
      const actorId = resolveBcsCandidateActorId(query, context);
      const response = await searchBcsCandidates(actorId, { q: keyword, purpose: 'discovery' }, signal);
      const userId = resolveLoggedInUserId(query, context) ?? '';
      const bots = response.data.items.flatMap((item) => {
        const bot = mapBcsBotCandidate(item, userId);
        return bot ? [bot] : [];
      });
      // 智能搜索是一次性批次（BCS 搜索容量上限 20）：只过滤本批、不补页；过滤为空也不改写
      // search_mode、不退回名称匹配或重新搜索。
      const keptBots = await filterPublicBotsByMetadata({
        bots,
        userId,
        backendEnabled: isPublicBotBackendEnhancementEnabled(userId),
        fetchMetadata: fetchPublicBotsMetadata,
        signal,
        onEnhancementUnavailable: notifyBotEnhancementUnavailable,
      });
      // 智能结果的关系回填从 BCS connections 与 pending 完成，不信任 Backend 状态。
      const items = await enrichRelationships(keptBots, resolveEnrichActor(query, context), signal);
      return { items, searchMode: response.data.search_mode };
    } catch (error) {
      return mapListError(error, 'Bot');
    }
  }

  async getBotProfile(bot: PublicBot, signal?: AbortSignal): Promise<PublicBotProfile> {
    // 申请弹窗画像以所点 `PublicBot` 摘要为兜底：Backend 未配置时不读旧 profile 端点；
    // Backend 启用时画像端点补齐引擎与已公开能力/技能，失败回退 BCS 摘要（404 → 由弹窗内
    // 按「无已公开能力」版式降级，不再判定 target_invalid）。
    const fallback = mapPublicBotCatalogSummaryToProfile(bot);
    if (!getCapabilities().getPublicBotBackendEnabled()) return fallback;
    try {
      const profile = mapBotProfileTransport((await getPublicBotProfile(bot.id, signal)).data);
      // Backend 画像不回写 Catalog 生命周期 status；候选 bcsStatus、shortProfile 等摘要字段原样保留。
      return {
        ...profile,
        ...(bot.description && !profile.description ? { description: bot.description } : {}),
        ...(bot.shortProfile ? { shortProfile: bot.shortProfile } : {}),
        ...(bot.friendRequestBotId ? { friendRequestBotId: bot.friendRequestBotId } : {}),
        ...(bot.isOwnedByLoggedInUser ? { isOwnedByLoggedInUser: true } : {}),
        ...(bot.bcsStatus ? { bcsStatus: bot.bcsStatus } : {}),
      };
    } catch (error) {
      return fallbackBotProfile(fallback, error);
    }
  }

  async requestBotFriendship(
    botId: string,
    context: HumanBotActionContext,
    friendRequestBotId?: string,
    fromActor?: FriendRequestActor,
  ): Promise<FriendRequestResult> {
    void context.actorId;
    try {
      const targetId = friendRequestBotId?.trim() || botId;
      // from_actor 默认为当前登录人类用户（协作广场）；对话协作由调用方按当前角色 tab 传入。
      const response = await createFriendConnectionRequest({
        to_actor: { type: 'bot', id: targetId },
        from_actor: fromActor ?? { type: 'human', id: context.userId },
      });
      if (isAceLoginResponse(response))
        throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
      if (response.code !== 20100)
        throw new CollaborationSquareError('protocol_error', '好友申请接口返回了无法识别的业务码');
      const status = response.data?.status?.trim().toLowerCase();
      if (status === 'pending') return { status: 'applying' };
      if (status === 'approved') return { status: 'friend' };
      if (status === 'public_no_edge') return { status: 'none' };
      throw new CollaborationSquareError('protocol_error', '好友申请接口返回了无法识别的状态');
    } catch (error) {
      return mapActionError(error, '申请好友权限');
    }
  }

  async openBotConversation(
    botId: string,
    context: HumanBotActionContext,
    options?: OpenBotConversationOptions,
  ): Promise<OpenBotConversationResult> {
    try {
      const { realBotId, ownerId } = splitBotId(botId);
      if (!realBotId || !context.userId.trim())
        throw new CollaborationSquareError('protocol_error', '创建 Bot 会话所需身份信息不完整');
      const response = await createBotSession(
        realBotId,
        {
          user_id: context.userId,
          ...(ownerId ? { owner_id: ownerId } : {}),
          // f_user_id 仅好友 Bot 会话需要；目标 Bot 归登录用户所有（自有 Bot）时不得携带。
          ...(options?.isOwnedByLoggedInUser ? {} : { f_user_id: context.userId }),
        },
        {},
      );
      if (isAceLoginResponse(response))
        throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
      if (isEnvelopeFailure(response))
        throw new CollaborationSquareError('protocol_error', '创建 Bot 会话接口返回了无法识别的业务码');
      const sessionId = response.data?.session_id;
      if (typeof sessionId !== 'string' || !sessionId.trim()) {
        throw new CollaborationSquareError('protocol_error', '创建 Bot 会话接口未返回 session_id');
      }
      return { sessionId };
    } catch (error) {
      return mapActionError(error, '创建 Bot 会话');
    }
  }

  async listGroupPage(
    query: PublicGroupSearchQuery = {},
    signal?: AbortSignal,
  ): Promise<CollaborationSquarePage<PublicGroup>> {
    try {
      const response = await listPublicGroups(
        {
          ...(query.search?.trim() ? { q: query.search.trim() } : {}),
          offset: query.offset ?? 0,
          limit: query.limit ?? 20,
        },
        signal,
      );
      const unnamedGroups: PublicGroup[] = [];
      const groups = response.data.items.flatMap((item) => {
        const group = mapPublicGroupCatalogDto(item);
        if (group && !item.driver_bot_name?.trim()) unnamedGroups.push(group);
        return group ? [group] : [];
      });
      // 优先使用目录中的名称；仅旧响应缺名称时才批量反查，不能覆盖新字段。
      const driverIds = [
        ...new Set(unnamedGroups.map((group) => group.driverBotUuid).filter((id): id is string => Boolean(id?.trim()))),
      ];
      if (driverIds.length > 0) {
        const nameMap = await resolveBotNames(driverIds);
        for (const group of unnamedGroups) {
          if (group.driverBotUuid) group.ownerBotName = nameMap[group.driverBotUuid]?.trim() || group.driverBotUuid;
        }
      }
      return { items: groups, total: response.data.total };
    } catch (error) {
      return mapListError(error, '协作群');
    }
  }

  async listGroups(query: PublicGroupSearchQuery = {}, signal?: AbortSignal): Promise<PublicGroup[]> {
    return (await this.listGroupPage(query, signal)).items;
  }

  async listGroupMembers(groupId: string): Promise<PublicGroupMembersDetail> {
    // 公开群成员通过群详情 GET /openapi/v1/collaboration/groups/{id} 的 participants 取得
    //（公开群目录无 participants，也无独立成员接口）。失败按列表错误映射抛 CollaborationSquareError。
    try {
      const resp = await getGroup(groupId);
      const participants = resp.data?.participants ?? [];
      const members = participants.map((p) => ({
        id: p.actor_id,
        avatarUrl: p.avatar_url,
        displayName: p.name?.trim() || p.actor_id,
        type: (p.actor_kind === 'human' ? 'human' : 'bot') as PublicGroupMember['type'],
        role: p.role,
      }));
      return {
        members,
        ownerUserName: resp.data?.driver_bot_owner_name?.trim() || resp.data?.driver_bot_owner?.trim() || '未公开',
      };
    } catch (error) {
      return mapListError(error, '协作群');
    }
  }

  async createGroupSession(
    groupId: string,
    context?: HumanBotActionContext,
    options?: { title?: string; query?: string; messageViewScope?: MessageViewScope },
  ): Promise<CreateSessionResult> {
    try {
      const response = await createGroupSessionRequest(groupId, {
        kind: 'chat',
        ...(context?.actorId ? { acting_bot_id: context.actorId } : {}),
        ...(options?.title ? { title: options.title } : {}),
        ...(options?.query ? { input: { query: options.query } } : {}),
        ...(options?.messageViewScope ? { message_view_scope: options.messageViewScope } : {}),
      });
      if (isAceLoginResponse(response))
        throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
      if (response.code !== 20100)
        throw new CollaborationSquareError('protocol_error', '创建协作群会话接口返回了无法识别的业务码');
      const session = response.data;
      if (!session || typeof session.session_id !== 'string' || !session.session_id.trim())
        throw new CollaborationSquareError('protocol_error', '创建协作群会话接口未返回 session_id');
      const sessionId = session.session_id;
      const caller = context?.actorId
        ? session.participants.find((participant) => participant.actor_id === context.actorId)
        : undefined;
      const defaultRole = mapSessionRole(caller?.role);
      if (context?.actorId && !defaultRole)
        throw new CollaborationSquareError('protocol_error', '创建协作群会话接口未返回当前用户角色');
      // 临时会话成员（consultant/observer）不是固定群成员，跳转后 membership 应为 session_only。
      const memberSource =
        caller && (caller.role === 'consultant' || caller.role === 'observer') ? ('session_temp' as const) : undefined;
      return { sessionId, ...(defaultRole ? { defaultRole } : {}), ...(memberSource ? { memberSource } : {}) };
    } catch (error) {
      return mapActionError(error, '创建协作群会话');
    }
  }

  // 任务广场：接入真实 BBS 接力公开任务端点 GET /openapi/v1/collaboration/tasks/bbs/list（1-based 分页 +
  // 可选 status / search_word 过滤，`total` 为过滤后行数）。分页 / 过滤 / 排序均在服务端，adapter 只做参数
  // 换算与展示名反查：offset/limit→page/page_size、search→search_word、广场态 status→BBS 原始态；publisher 与
  // assignee 经 bots/query 反查为展示名（复合 bot_id:owner 由 resolveBotNames 拆 realBotId 下发，按原始 id 回填）。
  async listPublicTasks(query: PublicTaskSearchQuery = {}, signal?: AbortSignal): Promise<PublicTaskPage> {
    try {
      const limit = typeof query.limit === 'number' && query.limit > 0 ? query.limit : undefined;
      const page = limit && typeof query.offset === 'number' ? Math.floor(query.offset / limit) + 1 : undefined;
      const searchWord = query.search?.trim() || undefined;
      const statusParam = query.status && query.status !== 'all' ? mapPlazaStatusToBbsStatus(query.status) : undefined;
      const resp = await listBbsTasks(
        {
          ...(page ? { page } : {}),
          ...(limit ? { page_size: limit } : {}),
          ...(searchWord ? { search_word: searchWord } : {}),
          ...(statusParam ? { status: statusParam } : {}),
        },
        signal,
      );
      if (isAceLoginResponse(resp)) {
        throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
      }
      if (resp.code !== 200000) {
        throw new CollaborationSquareError('protocol_error', '任务广场接口返回了无法识别的业务码');
      }
      const items = resp.data?.items ?? [];
      // 收集非空 publisher Bot ID，经 bots/query 批量反查展示名（与群主 driver_bot_uuid 反查同法）；
      // 查到的用名、查不到的兜底用 ID（由 mapBbsTaskItemDto 完成），publisher 为 null 不参与反查。
      const publisherIds = [
        ...new Set(
          items.map((item) => item.publisher).filter((id): id is string => typeof id === 'string' && id.trim() !== ''),
        ),
      ];
      const publisherNameMap = publisherIds.length > 0 ? await resolveBotNames(publisherIds) : {};
      // 认领者同理：收集已认领任务的 assignee_id 反查展示名。后端未给 assignee_name 时用反查名兜底，
      // 反查也查不到再兜底 assignee_id（由 mapBbsTaskItemDto 完成）。复合 bot_id:owner 由 resolveBotNames 拆 realBotId。
      const assigneeIds = [
        ...new Set(
          items
            .map((item) => item.assignee_id)
            .filter((id): id is string => typeof id === 'string' && id.trim() !== ''),
        ),
      ];
      const assigneeNameMap = assigneeIds.length > 0 ? await resolveBotNames(assigneeIds) : {};
      const mapped = items.flatMap((dto) => {
        const task = mapBbsTaskItemDto(dto, publisherNameMap, assigneeNameMap);
        return task ? [task] : [];
      });
      return { items: mapped, total: resp.data?.total ?? mapped.length };
    } catch (error) {
      return mapListError(error, '任务广场');
    }
  }

  // 任务详情：真实 BBS 端点不提供单点详情，Hook 改用已加载列表项填充详情；接口保留显式 unsupported，
  // 避免调用方误以为可取详情而发请求。参数对齐 Gateway 声明但不使用。
  async getPublicTask(_taskId: string, _signal?: AbortSignal): Promise<PublicTask> {
    void _taskId;
    void _signal;
    return unsupported('任务详情改用列表项填充，真实 BBS 端点不提供单点详情');
  }
}

export const collaborationSquareApiAdapter = new CollaborationSquareApiAdapter();
