import type { IdentityReachability, IdentityStatus } from '@/domain/collaboration';
import { resolveOpenApiUserId } from '@/domain/userIdentity';
import { listBotMetadata, type BotMetadataDto } from '@/services/backendApi/bots/botController';
import {
  createBotFriendRequest,
  listBotCandidates,
  listMyBots,
  queryCollaborationBots,
  type CollaborationBotDto,
} from '@/services/backendApi/collaboration/collaborationBotController';
import {
  listFriendConnections,
  type FriendConnectionSummaryDto,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import type { DomainError, DomainResult } from './identityService';

export interface CollaborationBotView {
  id: string;
  name: string;
  avatarUrl?: string;
  summary?: string;
  online: boolean;
  status: IdentityStatus;
  reachability: IdentityReachability;
  visibility: 'public' | 'protected' | 'private';
  isFriend?: boolean;
  engine?: string;
  botType?: string;
  detailsResolved?: boolean;
}

export interface CollaborationBotPage {
  items: CollaborationBotView[];
  total: number;
  offset: number;
  limit: number;
  hasMore: boolean;
}

interface ListFriendsOptions {
  actorType?: 'human' | 'bot';
  detailSource?: 'metadata' | 'collaboration';
  offset?: number;
  limit?: number;
}

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: false };
}

function mapBot(dto: CollaborationBotDto, isFriend?: boolean): CollaborationBotView {
  return {
    id: dto.bot_id,
    name: dto.name ?? dto.bot_id,
    avatarUrl: dto.avatar_url,
    summary: dto.descriptor?.summary,
    online: dto.status === 'online' && dto.reachability !== 'unreachable',
    status: dto.status === 'hidden' ? 'hidden' : 'online',
    reachability: dto.reachability === 'unreachable' ? 'unreachable' : 'reachable',
    visibility: dto.visibility ?? 'private',
    engine: dto.engine,
    botType: dto.bot_type,
    ...(isFriend !== undefined ? { isFriend } : {}),
  };
}

function mapBotMetadata(dto: BotMetadataDto, id: string): CollaborationBotView {
  return {
    id,
    name: dto.bot_name || dto.bot_id,
    online: dto.status === 'ACTIVE' || dto.status === 'online',
    status: 'online',
    reachability: 'reachable',
    visibility: 'private',
    isFriend: true,
    engine: dto.engine,
    botType: dto.bot_type,
  };
}

function splitCompoundBotId(botId: string): { botId: string; ownerId?: string } {
  const index = botId.indexOf(':');
  if (index < 0) return { botId };
  return { botId: botId.slice(0, index), ownerId: botId.slice(index + 1) };
}

function toUnavailableFriendBot(id: string, name?: string): CollaborationBotView {
  return {
    id,
    name: name ?? id,
    online: true,
    status: 'online',
    reachability: 'reachable',
    visibility: 'private',
    isFriend: true,
  };
}

function toUnknownFriendBot(id: string): CollaborationBotView {
  return {
    id,
    name: id,
    online: false,
    status: 'hidden',
    reachability: 'reachable',
    visibility: 'private',
    isFriend: true,
    detailsResolved: false,
  };
}

async function loadMetadataFriendDetails(
  actorId: string,
  friendIds: string[],
  namesById: Map<string, string | undefined>,
): Promise<CollaborationBotView[]> {
  const botResp = await listBotMetadata(
    { user_id: resolveOpenApiUserId(actorId), page: 1, page_size: friendIds.length },
    {
      bots: friendIds.map((friendId) => {
        const { botId, ownerId } = splitCompoundBotId(friendId);
        return ownerId
          ? { bot_id: botId, owner_id: ownerId }
          : { bot_id: botId, owner_id: resolveOpenApiUserId(actorId) };
      }),
    },
  );
  const detailsByKey = new Map(
    (botResp.data?.items ?? []).map((bot) => [bot.owner_id ? `${bot.bot_id}:${bot.owner_id}` : bot.bot_id, bot]),
  );
  return friendIds.map((friendId) => {
    const detail = detailsByKey.get(friendId);
    return detail
      ? { ...mapBotMetadata(detail, friendId), name: detail.bot_name || namesById.get(friendId) || friendId }
      : toUnavailableFriendBot(friendId, namesById.get(friendId));
  });
}

async function loadCollaborationFriendDetails(
  friendIds: string[],
  namesById: Map<string, string | undefined>,
): Promise<CollaborationBotView[]> {
  const botResp = await queryCollaborationBots({ bot_ids: friendIds });
  const detailsByBotId = new Map((botResp.data?.items ?? []).map((bot) => [bot.bot_id, bot]));
  return friendIds.map((friendId) => {
    const detail = detailsByBotId.get(friendId);
    return detail
      ? { ...mapBot(detail, true), id: friendId, name: detail.name || namesById.get(friendId) || friendId }
      : toUnknownFriendBot(friendId);
  });
}

/**
 * 候选 Bot 数据层：好友列表先读 Friend Connections，再按调用场景补齐 bot 详情。
 * 组件只消费 DomainResult<CollaborationBotView[]>，不直接依赖 DTO。
 */
export const collaborationCandidateService = {
  async listMine(opts: { offset?: number; limit?: number } = {}): Promise<DomainResult<CollaborationBotPage>> {
    try {
      const limit = 100;
      const offset = opts.offset ?? 0;
      // mine 返回当前身份与自有 Bot，头部过滤掉 human，只把 Bot 提供给出协作选择器。
      const resp = await listMyBots({ offset, limit });
      const items = (resp.data?.items ?? []).filter((bot) => bot.kind === 'bot').map((bot) => mapBot(bot));
      return {
        ok: true,
        data: { items, total: items.length, offset, limit, hasMore: false },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('COLLABORATION_MINE_LOAD_FAILED', '加载我的 Bot 失败，请稍后重试。'),
      };
    }
  },

  async listFriends(actorId: string, opts: ListFriendsOptions = {}): Promise<DomainResult<CollaborationBotPage>> {
    try {
      const offset = opts.offset ?? 0;
      const limit = opts.limit ?? 50;
      const actorType = opts.actorType ?? (actorId.startsWith('human_') ? 'human' : 'bot');
      const relResp = await listFriendConnections<FriendConnectionSummaryDto>({
        actor_type: actorType,
        actor_id: actorType === 'human' ? resolveOpenApiUserId(actorId) : actorId,
      });
      const relationItems = relResp.data?.items ?? [];
      const friendIds = Array.from(new Set(relationItems.map((item) => item.actor?.id).filter(Boolean) as string[]));
      const namesById = new Map<string, string | undefined>();
      relationItems.forEach((item) => {
        if (item.actor?.id && !namesById.has(item.actor.id)) namesById.set(item.actor.id, item.name);
      });
      const total = relResp.data?.total ?? friendIds.length;
      if (friendIds.length === 0) {
        return { ok: true, data: { items: [], total, offset, limit, hasMore: false } };
      }

      const items = await (opts.detailSource === 'collaboration'
        ? loadCollaborationFriendDetails(friendIds, namesById)
        : loadMetadataFriendDetails(actorId, friendIds, namesById));
      return {
        ok: true,
        data: { items, total, offset, limit, hasMore: false },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('COLLABORATION_FRIENDS_LOAD_FAILED', '加载好友 Bot 失败，请稍后重试。'),
      };
    }
  },

  async listCandidates(
    actorId: string,
    opts: { name?: string; offset?: number; limit?: number } = {},
  ): Promise<DomainResult<CollaborationBotPage>> {
    try {
      const offset = opts.offset ?? 0;
      const limit = opts.limit ?? 50;
      const resp = await listBotCandidates(actorId, {
        purpose: 'collaboration',
        name: opts.name?.trim() || undefined,
        offset,
        limit,
      });
      const items = (resp.data?.items ?? [])
        .filter((item) => item.bot?.kind === 'bot')
        .map((item) => mapBot(item.bot, item.is_friend));
      const total = resp.data?.total ?? items.length;
      return {
        ok: true,
        data: { items, total, offset, limit, hasMore: offset + items.length < total },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('COLLABORATION_CANDIDATES_LOAD_FAILED', '加载可协作 Bot 失败，请稍后重试。'),
      };
    }
  },

  /** 添加好友弹窗：purpose=discovery 查询可建立好友关系的公开 Bot。 */
  async listDiscoveryBots(
    actorId: string,
    opts: { name?: string; offset?: number; limit?: number } = {},
  ): Promise<DomainResult<CollaborationBotPage>> {
    try {
      const offset = opts.offset ?? 0;
      const limit = opts.limit ?? 50;
      const resp = await listBotCandidates(actorId, {
        purpose: 'discovery',
        name: opts.name?.trim() || undefined,
        offset,
        limit,
      });
      const items = (resp.data?.items ?? [])
        .filter((item) => item.bot?.kind === 'bot')
        .map((item) => mapBot(item.bot, item.is_friend));
      const total = resp.data?.total ?? items.length;
      return {
        ok: true,
        data: { items, total, offset, limit, hasMore: offset + items.length < total },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('COLLABORATION_DISCOVERY_LOAD_FAILED', '加载 Bot 广场失败，请稍后重试。'),
      };
    }
  },

  /** 发送好友申请：POST /openapi/v1/collaboration/bots/{actorId}/friend-requests。 */
  async sendFriendRequest(actorId: string, toBotUuid: string): Promise<DomainResult<null>> {
    try {
      await createBotFriendRequest(actorId, { to_bot_uuid: toBotUuid });
      return { ok: true, data: null };
    } catch {
      return {
        ok: false,
        error: toDomainError('FRIEND_REQUEST_FAILED', '发送好友申请失败，请稍后重试。'),
      };
    }
  },
};
