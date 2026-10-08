import {
  listBotSessionMessages,
  listBotSessions,
  type BotMessageDto,
} from '@/services/backendApi/bots/privateBotSessionController';
import {
  queryCollaborationBots,
  type CollaborationBotDto,
} from '@/services/backendApi/collaboration/collaborationBotController';
import {
  listFriendConnections,
  type FriendConnectionActorType,
  type FriendConnectionDto,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { mapBotSessionMessages } from './botSessionMessageMapper';
import { mapBotSessionSummary } from './botSessionService';
import type { DomainError, DomainResult } from './identityService';

export type BotFriendActorType = 'human' | 'bot';

export interface BotIdentityFriendView {
  actorType: BotFriendActorType;
  actorId: string;
  queryId: string;
  displayName: string;
  online: boolean;
  detailsResolved: boolean;
  disabledReason?: string;
}

export interface BotFriendSessionView {
  sessionId: string;
  friendUserId: string;
  title: string;
  messageCount: number;
  gmtModified: string;
  gmtCreate: string;
}

export interface BotFriendPage<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
}

export interface BotFriendConversationContext {
  currentBotIdentityId: string;
  currentBotId: string;
  ownerUserId: string;
  friendUserId: string;
}

export interface BotFriendMessagePageView {
  messages: ReturnType<typeof mapBotSessionMessages>;
  total: number;
  rawCount: number;
}

export interface BotFriendDirectoryResult {
  human: DomainResult<BotFriendPage<BotIdentityFriendView>>;
  bot: DomainResult<BotFriendPage<BotIdentityFriendView>>;
}

export const BOT_FRIEND_PAGE_SIZE = 100;
export const BOT_FRIEND_SESSION_PAGE_SIZE = 10;
export const BOT_FRIEND_MESSAGE_PAGE_SIZE = 50;
export const BOT_FRIEND_DISABLED_REASON = '暂不支持查看 Bot 好友对话';

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: true };
}

export function normalizeFriendUserId(id: string): string {
  return id.trim().replace(/^human_/, '');
}

export function toFriendQueryId(type: BotFriendActorType, id: string): string {
  const normalized = id.trim();
  return type === 'human' ? `human_${normalizeFriendUserId(normalized)}` : normalized;
}

export function resolveBotFriendContext(
  currentBotIdentityId: string,
  friendUserId: string,
): BotFriendConversationContext | null {
  const separator = currentBotIdentityId.indexOf(':');
  const currentBotId = separator > 0 ? currentBotIdentityId.slice(0, separator).trim() : '';
  const ownerUserId = separator > 0 ? currentBotIdentityId.slice(separator + 1).trim() : '';
  const normalizedFriendUserId = normalizeFriendUserId(friendUserId);
  if (!currentBotId || !ownerUserId || !normalizedFriendUserId) return null;
  return {
    currentBotIdentityId,
    currentBotId,
    ownerUserId,
    friendUserId: normalizedFriendUserId,
  };
}

interface RelationPage {
  items: FriendConnectionDto[];
  total: number;
}

async function loadAllRelations(
  botIdentityId: string,
  targetType: FriendConnectionActorType,
  signal: AbortSignal,
): Promise<RelationPage> {
  const items: FriendConnectionDto[] = [];
  let page = 1;
  let total = 0;
  while (page === 1 || items.length < total) {
    const response = await listFriendConnections<FriendConnectionDto>(
      {
        actor_type: 'bot',
        actor_id: botIdentityId,
        target_type: targetType,
        page,
        page_size: BOT_FRIEND_PAGE_SIZE,
      },
      signal,
    );
    const current = response.data?.items ?? [];
    total = response.data?.total ?? current.length;
    items.push(...current);
    if (current.length === 0) break;
    page += 1;
  }
  return { items, total };
}

function uniqueRelations(items: FriendConnectionDto[], targetType: BotFriendActorType): FriendConnectionDto[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const id = item.actor?.id?.trim();
    if (!id || item.actor.type !== targetType || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function mapFriendSection(
  targetType: BotFriendActorType,
  relations: RelationPage,
  detailsById: Map<string, CollaborationBotDto>,
): BotFriendPage<BotIdentityFriendView> {
  const unique = uniqueRelations(relations.items, targetType);
  return {
    items: unique.map((relation) => {
      const rawId = relation.actor.id.trim();
      const actorId = targetType === 'human' ? normalizeFriendUserId(rawId) : rawId;
      const queryId = toFriendQueryId(targetType, actorId);
      const detail = detailsById.get(queryId);
      return {
        actorType: targetType,
        actorId,
        queryId,
        displayName: detail?.name?.trim() || actorId,
        online: detail
          ? detail.status === 'online' && detail.reachability !== 'unreachable'
          : Boolean(relation.is_online),
        detailsResolved: Boolean(detail),
        ...(targetType === 'bot' ? { disabledReason: BOT_FRIEND_DISABLED_REASON } : {}),
      };
    }),
    total: relations.total,
    page: 1,
    pageSize: BOT_FRIEND_PAGE_SIZE,
    hasMore: false,
  };
}

function sectionFailure(targetType: BotFriendActorType): DomainResult<BotFriendPage<BotIdentityFriendView>> {
  return {
    ok: false,
    error: toDomainError(
      targetType === 'human' ? 'BOT_FRIEND_USERS_LOAD_FAILED' : 'BOT_FRIEND_BOTS_LOAD_FAILED',
      targetType === 'human' ? '加载好友用户失败，请稍后重试。' : '加载好友 Bot 失败，请稍后重试。',
    ),
  };
}

async function resolveDetails(
  relations: RelationPage[],
  signal: AbortSignal,
): Promise<Map<string, CollaborationBotDto>> {
  const queryIds = Array.from(
    new Set(
      relations.flatMap((relation) =>
        relation.items.map((item) => toFriendQueryId(item.actor.type, item.actor.id)).filter(Boolean),
      ),
    ),
  );
  if (queryIds.length === 0) return new Map();
  try {
    const response = await queryCollaborationBots({ bot_ids: queryIds }, signal);
    return new Map((response.data?.items ?? []).map((item) => [item.bot_id, item]));
  } catch {
    return new Map();
  }
}

async function loadSection(
  botIdentityId: string,
  targetType: BotFriendActorType,
  signal?: AbortSignal,
): Promise<DomainResult<BotFriendPage<BotIdentityFriendView>>> {
  const requestSignal = signal ?? new AbortController().signal;
  try {
    const relations = await loadAllRelations(botIdentityId, targetType, requestSignal);
    const details = await resolveDetails([relations], requestSignal);
    return { ok: true, data: mapFriendSection(targetType, relations, details) };
  } catch {
    return sectionFailure(targetType);
  }
}

export const botFriendConversationService = {
  async loadDirectory(botIdentityId: string, signal?: AbortSignal): Promise<BotFriendDirectoryResult> {
    const requestSignal = signal ?? new AbortController().signal;
    const [humanSettled, botSettled] = await Promise.allSettled([
      loadAllRelations(botIdentityId, 'human', requestSignal),
      loadAllRelations(botIdentityId, 'bot', requestSignal),
    ]);
    const successfulRelations = [humanSettled, botSettled]
      .filter((result): result is PromiseFulfilledResult<RelationPage> => result.status === 'fulfilled')
      .map((result) => result.value);
    const details = await resolveDetails(successfulRelations, requestSignal);
    return {
      human:
        humanSettled.status === 'fulfilled'
          ? { ok: true, data: mapFriendSection('human', humanSettled.value, details) }
          : sectionFailure('human'),
      bot:
        botSettled.status === 'fulfilled'
          ? { ok: true, data: mapFriendSection('bot', botSettled.value, details) }
          : sectionFailure('bot'),
    };
  },

  loadSection,

  async listSessionsPage(
    botIdentityId: string,
    friendUserId: string,
    page = 1,
    pageSize = BOT_FRIEND_SESSION_PAGE_SIZE,
  ): Promise<DomainResult<BotFriendPage<BotFriendSessionView>>> {
    const context = resolveBotFriendContext(botIdentityId, friendUserId);
    if (!context) {
      return {
        ok: false,
        error: toDomainError('BOT_FRIEND_CONTEXT_INVALID', '当前 Bot 或好友用户信息无效。'),
      };
    }
    try {
      const response = await listBotSessions(context.currentBotId, {
        user_id: context.ownerUserId,
        owner_id: context.ownerUserId,
        f_user_id: context.friendUserId,
        page,
        page_size: pageSize,
      });
      const source = response.data?.items ?? [];
      const total = response.data?.total ?? source.length;
      return {
        ok: true,
        data: {
          items: source.map((item) => ({ ...mapBotSessionSummary(item), friendUserId: context.friendUserId })),
          total,
          page,
          pageSize,
          hasMore: page * pageSize < total,
        },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('BOT_FRIEND_SESSIONS_LOAD_FAILED', '加载好友用户会话失败，请稍后重试。'),
      };
    }
  },

  async listMessagesPage(
    botIdentityId: string,
    friendUserId: string,
    sessionId: string,
    page = 1,
    pageSize = BOT_FRIEND_MESSAGE_PAGE_SIZE,
  ): Promise<DomainResult<BotFriendMessagePageView>> {
    const context = resolveBotFriendContext(botIdentityId, friendUserId);
    if (!context || !sessionId.trim()) {
      return {
        ok: false,
        error: toDomainError('BOT_FRIEND_CONTEXT_INVALID', '当前 Bot、好友用户或会话信息无效。'),
      };
    }
    try {
      const response = await listBotSessionMessages(context.currentBotId, sessionId, {
        user_id: context.ownerUserId,
        owner_id: context.ownerUserId,
        f_user_id: context.friendUserId,
        page,
        page_size: pageSize,
      });
      const source = (response.data?.items ?? []) as BotMessageDto[];
      return {
        ok: true,
        data: {
          messages: mapBotSessionMessages(source),
          total: response.data?.total ?? source.length,
          rawCount: source.length,
        },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('BOT_FRIEND_MESSAGES_LOAD_FAILED', '加载好友用户历史消息失败，请稍后重试。'),
      };
    }
  },
};
