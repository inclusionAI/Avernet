import { mapPublicBotCatalogDto, mapPublicGroupCatalogDto } from '@/domain/collaborationSquare/mapper';
import type {
  CollaborationSquarePage,
  CreateSessionResult,
  FriendRequestActor,
  FriendRequestResult,
  HumanBotActionContext,
  OpenBotConversationResult,
  PublicBot,
  PublicBotDiscoveryQuery,
  PublicBotProfile,
  PublicBotSearchQuery,
  PublicGroup,
  PublicGroupMember,
  PublicGroupSearchQuery,
} from '@/domain/collaborationSquare/types';
import { getPublicBotTargetId } from '@/domain/collaborationSquare/types';
import { createBotSession } from '@/services/backendApi/bots/privateBotSessionController';
import { createFriendConnectionRequest } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { getGroup, listPublicGroups } from '@/services/backendApi/collaboration/collaborationGroupController';
import { discoverPublicBots, searchPublicBots } from '@/services/backendApi/collaboration/publicBotController';
import { createSession as createGroupSessionRequest } from '@/services/backendApi/collaboration/sessionController';
import { isEnvelopeFailure } from '@/services/backendApi/types';
import {
  isAceLoginResponse,
  mapActionError,
  mapListError,
  mapSessionRole,
  resolveBotNames,
  splitBotId,
  unsupported,
} from './collaborationSquareApiHelpers';
import { CollaborationSquareError } from './collaborationSquareError';
import type { CollaborationSquareGateway } from './collaborationSquareGateway';
import { listFriendBotRelationships, listPendingFriendBotRelationships } from './friendConnectionRelationships';

async function enrichRelationships(bots: PublicBot[], actor?: FriendRequestActor, signal?: AbortSignal) {
  if (!actor) return bots;
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
      bot.isOwnedByViewer || bot.relationshipStatus === 'friend'
        ? bot.relationshipStatus
        : applyingIds.has(getPublicBotTargetId(bot))
        ? ('applying' as const)
        : bot.relationshipStatus,
  }));
}

/** 关系回填的 viewer userId：仅 human viewer 返回其 id，bot viewer 回退登录人类。 */
function resolveViewerUserId(
  query: { viewerActorType?: 'human' | 'bot'; viewerActorId?: string },
  context?: HumanBotActionContext,
): string | undefined {
  if (query.viewerActorType) return query.viewerActorType === 'human' ? query.viewerActorId : undefined;
  return context?.userId;
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

/** 接入公开 Bot Search/Discovery、Human→Bot 关系/会话与公开群列表；画像和群写能力保持显式 unsupported。 */
export class CollaborationSquareApiAdapter implements CollaborationSquareGateway {
  async listBotPage(
    query: PublicBotSearchQuery = {},
    context?: HumanBotActionContext,
    signal?: AbortSignal,
  ): Promise<CollaborationSquarePage<PublicBot>> {
    try {
      const response = await searchPublicBots(
        {
          ...(query.search?.trim() ? { search: query.search.trim() } : {}),
          page: query.page ?? 1,
          page_size: query.pageSize ?? 20,
          ...(query.viewerActorType ? { viewer_actor_type: query.viewerActorType } : {}),
          ...(query.viewerActorId ? { viewer_actor_id: query.viewerActorId } : {}),
        },
        signal,
      );
      const viewerUserId = resolveViewerUserId(query, context);
      const bots = response.data.items.flatMap((item) => {
        const bot = mapPublicBotCatalogDto(item, viewerUserId);
        return bot ? [bot] : [];
      });
      // Search 已返回 bot_uuid/is_friend；仅补读 pending 申请，以保留刷新后的“申请中”状态。
      // 回填 actor 跟随 viewer（对话协作当前角色 tab），缺省为登录人类（协作广场）。
      return {
        items: await enrichPendingRelationships(bots, resolveEnrichActor(query, context), signal),
        total: response.data.total,
      };
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
  ): Promise<PublicBot[]> {
    try {
      const response = await discoverPublicBots(
        {
          keyword: query.keyword.trim(),
          top_k: query.topK ?? 20,
          min_score: query.minScore ?? 0.1,
          runtime_state: query.runtimeState ?? 'online',
          ...(query.viewerActorType ? { viewer_actor_type: query.viewerActorType } : {}),
          ...(query.viewerActorId ? { viewer_actor_id: query.viewerActorId } : {}),
        },
        signal,
      );
      const viewerUserId = resolveViewerUserId(query, context);
      const bots = response.data.items.flatMap((item) => {
        const bot = mapPublicBotCatalogDto(item, viewerUserId);
        return bot ? [bot] : [];
      });
      return await enrichRelationships(bots, resolveEnrichActor(query, context), signal);
    } catch (error) {
      return mapListError(error, 'Bot');
    }
  }

  async getBotProfile(botId: string): Promise<PublicBotProfile> {
    void botId;
    return unsupported('Bot 画像接口尚未接入');
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

  async openBotConversation(botId: string, context: HumanBotActionContext): Promise<OpenBotConversationResult> {
    try {
      const { realBotId, ownerId } = splitBotId(botId);
      if (!realBotId || !context.userId.trim())
        throw new CollaborationSquareError('protocol_error', '创建 Bot 会话所需身份信息不完整');
      const response = await createBotSession(
        realBotId,
        {
          user_id: context.userId,
          ...(ownerId ? { owner_id: ownerId } : {}),
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
      const groups = response.data.items.flatMap((item) => {
        const group = mapPublicGroupCatalogDto(item);
        return group ? [group] : [];
      });
      // 公开群目录响应无 participants，群主名无法从列表直接得出；收集本页 driver_bot_uuid 经
      // bots/query 批量反查 name 回填 ownerBotName。查不到的（如 driver bot 已删除）保持兜底“未公开”。
      const driverIds = [
        ...new Set(groups.map((g) => g.driverBotUuid).filter((id): id is string => !!id && id.trim() !== '')),
      ];
      if (driverIds.length > 0) {
        const nameMap = await resolveBotNames(driverIds);
        for (const group of groups) {
          if (!group.driverBotUuid) continue;
          // 查到则展示名称；查不到（如 driver bot 已删除）则展示 uuid，不回退"未公开"。
          const name = nameMap[group.driverBotUuid];
          group.ownerBotName = name ?? group.driverBotUuid;
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

  async listGroupMembers(groupId: string): Promise<PublicGroupMember[]> {
    // 公开群成员通过群详情 GET /openapi/v1/collaboration/groups/{id} 的 participants 取得
    //（公开群目录无 participants，也无独立成员接口）。失败按列表错误映射抛 CollaborationSquareError。
    try {
      const resp = await getGroup(groupId);
      const participants = resp.data?.participants ?? [];
      return participants.map((p) => ({
        id: p.actor_id,
        displayName: p.name?.trim() || p.actor_id,
        type: (p.actor_kind === 'human' ? 'human' : 'bot') as PublicGroupMember['type'],
        role: p.role,
      }));
    } catch (error) {
      return mapListError(error, '协作群');
    }
  }

  async createGroupSession(
    groupId: string,
    context?: HumanBotActionContext,
    options?: { title?: string; query?: string },
  ): Promise<CreateSessionResult> {
    try {
      const response = await createGroupSessionRequest(groupId, {
        kind: 'chat',
        ...(context?.actorId ? { acting_bot_id: context.actorId } : {}),
        ...(options?.title ? { title: options.title } : {}),
        ...(options?.query ? { input: { query: options.query } } : {}),
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
}

export const collaborationSquareApiAdapter = new CollaborationSquareApiAdapter();
