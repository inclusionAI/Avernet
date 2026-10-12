// 管理 Bot 「查看他人对话」(origin=others)只读 Service(设计依据:2026-09-24 conversation navigation refactor)。
// 以 bot.botId = realBotId:ownerId 复合 id 拆出所有者,查询该 Bot 的好友 Human 用户与会话/消息历史。
// 仅读:不提供任何 create/update/delete/favorite 方法,也不读取 Store / 路由。
import type {
  ConversationMessagePageView,
  ConversationSessionListState,
  ConversationUserView,
} from '@/domain/conversation/types';
import {
  listBotSessionMessages,
  listBotSessions,
  type BotMessageDto,
} from '@/services/backendApi/bots/privateBotSessionController';
import {
  listFriendConnections,
  type FriendConnectionSummaryDto,
} from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { BOT_FRIEND_PAGE_SIZE, normalizeFriendUserId } from './botFriendConversationService';
import { mapBotSessionMessages } from './botSessionMessageMapper';
import {
  BOT_MESSAGE_PAGE_SIZE,
  BOT_SESSION_PAGE_SIZE,
  mapBotSessionSummary,
  splitBotId,
  type ChatBotView,
} from './botSessionService';
import type { DomainError, DomainResult } from './identityService';

const INVALID_CONTEXT_ERROR: DomainError = {
  code: 'CONVERSATION_CONTEXT_INVALID',
  friendlyMessage: '当前 Bot 或好友用户信息无效。',
  canRetry: false,
};

// 临时关闭团队 Bot 他人会话，避免历史 URL/缓存绕过禁用入口继续请求未就绪后端。
const TEAM_OTHERS_UNAVAILABLE: DomainError = {
  code: 'TEAM_OTHERS_UNAVAILABLE',
  friendlyMessage: '他人发起的会话功能开发中',
  canRetry: false,
};

/** 管理 Bot 上下文:复合 botId 拆出 realBotId/ownerId,所有者不可缺省。 */
function resolveOwnerContext(bot: ChatBotView): { realBotId: string; ownerId: string } | null {
  const { realBotId, ownerId } = splitBotId(bot.botId);
  if (!realBotId.trim() || !ownerId?.trim()) return null;
  return { realBotId, ownerId };
}

function toDomainError(code: string, friendlyMessage: string): DomainError {
  return { code, friendlyMessage, canRetry: true };
}

interface ConversationFriendRelation {
  actor: { type: string; id: string };
  name?: string;
}

/** 好友连接项 → ConversationUserView:human_ 前缀归一化,详情名缺失回退纯用户 ID;非 human 返回 null。 */
export function mapConversationFriendUser(item: ConversationFriendRelation): ConversationUserView | null {
  if (item.actor?.type !== 'human') return null;
  const userId = normalizeFriendUserId(item.actor.id);
  // normalizeFriendUserId 去前缀不校验空段,这里兜底防御。
  if (!userId) return null;
  return { userId, displayName: item.name?.trim() || userId };
}

async function loadAllFriendUsers(botIdentityId: string, signal: AbortSignal): Promise<ConversationUserView[]> {
  const items: FriendConnectionSummaryDto[] = [];
  let page = 1;
  let total = 0;
  while (page === 1 || items.length < total) {
    const response = await listFriendConnections<FriendConnectionSummaryDto>(
      {
        actor_type: 'bot',
        actor_id: botIdentityId,
        target_type: 'human',
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
  const seen = new Set<string>();
  const users: ConversationUserView[] = [];
  for (const item of items) {
    const user = mapConversationFriendUser(item);
    if (!user || seen.has(user.userId)) continue;
    seen.add(user.userId);
    users.push(user);
  }
  return users;
}

export const managedBotConversationService = {
  /** 管理 Bot 的好友 Human 用户列表(bot actor 查询,signal 透传给 AbortController 取消)。 */
  async loadFriendUsers(bot: ChatBotView, signal?: AbortSignal): Promise<DomainResult<ConversationUserView[]>> {
    if (bot.isTeamBot) return { ok: false, error: TEAM_OTHERS_UNAVAILABLE };
    const requestSignal = signal ?? new AbortController().signal;
    try {
      const users = await loadAllFriendUsers(bot.botId, requestSignal);
      return { ok: true, data: users };
    } catch {
      return {
        ok: false,
        error: toDomainError('CONVERSATION_FRIEND_USERS_LOAD_FAILED', '加载好友用户失败，请稍后重试。'),
      };
    }
  },

  /** 该 Bot 视角下某好友用户的会话分页(只读);返回值即 Catalog 缓存态,Hook 只做装饰。 */
  async listOtherSessions(
    bot: ChatBotView,
    friendUserId: string,
    page: number = 1,
    pageSize: number = BOT_SESSION_PAGE_SIZE,
  ): Promise<DomainResult<ConversationSessionListState>> {
    if (bot.isTeamBot) return { ok: false, error: TEAM_OTHERS_UNAVAILABLE };
    const context = resolveOwnerContext(bot);
    const friendId = normalizeFriendUserId(friendUserId);
    if (!context || !friendId) {
      return { ok: false, error: INVALID_CONTEXT_ERROR };
    }
    try {
      const response = await listBotSessions(context.realBotId, {
        user_id: context.ownerId,
        owner_id: context.ownerId,
        f_user_id: friendId,
        page,
        page_size: pageSize,
      });
      const source = response.data?.items ?? [];
      const total = response.data?.total ?? source.length;
      return {
        ok: true,
        data: {
          items: source.map((item) => ({ ...mapBotSessionSummary(item), botId: bot.botId })),
          page,
          total,
          hasMore: page * pageSize < total,
          loading: false,
          error: null,
          isLoadingMore: false,
          loadMoreError: null,
        },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('CONVERSATION_OTHER_SESSIONS_LOAD_FAILED', '加载好友用户会话失败，请稍后重试。'),
      };
    }
  },

  /** 该 Bot 视角下某好友用户历史消息分页(只读,旧→新升序)。 */
  async listOtherMessages(
    bot: ChatBotView,
    friendUserId: string,
    sessionId: string,
    page: number = 1,
    pageSize: number = BOT_MESSAGE_PAGE_SIZE,
  ): Promise<DomainResult<ConversationMessagePageView>> {
    if (bot.isTeamBot) return { ok: false, error: TEAM_OTHERS_UNAVAILABLE };
    const context = resolveOwnerContext(bot);
    const friendId = normalizeFriendUserId(friendUserId);
    if (!context || !friendId || !sessionId.trim()) {
      return { ok: false, error: INVALID_CONTEXT_ERROR };
    }
    try {
      const response = await listBotSessionMessages(context.realBotId, sessionId, {
        user_id: context.ownerId,
        owner_id: context.ownerId,
        f_user_id: friendId,
        page,
        page_size: pageSize,
      });
      const source = (response.data?.items ?? []) as BotMessageDto[];
      const total = response.data?.total ?? source.length;
      return {
        ok: true,
        data: {
          messages: mapBotSessionMessages(source),
          page,
          total,
          rawCount: source.length,
          hasMore: page * pageSize < total,
        },
      };
    } catch {
      return {
        ok: false,
        error: toDomainError('CONVERSATION_OTHER_MESSAGES_LOAD_FAILED', '加载好友用户历史消息失败，请稍后重试。'),
      };
    }
  },
};
