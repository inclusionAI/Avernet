// 对话工作台目录与会话 Service(设计依据:docs/specs/2026-09-24-workspace-conversation-navigation-refactor.md)。
// 组合 botSessionService / collaborationCandidateService 的既有映射,不重复协议转换。
// 由调用方(Hook)传入已鉴权的 Human ID,本 Service 不读取任何 Store / 路由。
import { supportsBotSessionFavorites } from '@/domain/botEngine';
import type { ConversationSessionScope } from '@/domain/conversation/types';
import {
  BOT_SESSION_PAGE_SIZE,
  botSessionService,
  splitBotId,
  type BotSessionPageView,
  type ChatBotView,
} from './botSessionService';
import { collaborationCandidateService, type CollaborationBotView } from './collaborationCandidateService';
import { conversationFavoriteService } from './conversationFavoriteService';
import type { DomainResult } from './identityService';

export interface ConversationDirectoryResult {
  /** 当前用户管理的可直接单聊 Bot(AgentCoding Bot 已剔除)。 */
  managedBots: ChatBotView[];
  /** Human→Bot 好友 Bot,统一带 isFriendBot 标记。 */
  friendBots: ChatBotView[];
  /** 是否存在独立入口消费的 AgentCoding Bot(供入口降级展示)。 */
  hasAgentCodingBots: boolean;
}

function toConversationFriendBotView(view: CollaborationBotView): ChatBotView {
  const { realBotId, ownerId } = splitBotId(view.id);
  return {
    botId: view.id,
    realBotId,
    ownerId,
    displayName: view.name,
    avatarUrl: view.avatarUrl,
    online: view.online,
    reachability: view.reachability,
    chatable: view.detailsResolved !== false,
    engine: view.engine,
    botType: view.botType,
    isFriendBot: true,
  };
}

export const conversationService = {
  /** 会话目录:管理 Bot 走自有 Bot 接口,好友 Bot 走 Friend Connections + metadata 补详情。 */
  async listDirectory(userId: string): Promise<DomainResult<ConversationDirectoryResult>> {
    const [ownedResult, friendsResult] = await Promise.all([
      botSessionService.listOwnedBotsWithMeta(userId),
      collaborationCandidateService.listFriends(userId, {
        actorType: 'human',
        offset: 0,
        limit: 100,
      }),
    ]);
    if (!ownedResult.ok) return { ok: false, error: ownedResult.error };
    // 好友 Bot 加载失败降级为空列表,管理 Bot 目录仍可用;失败提示由 Hook 层负责。
    return {
      ok: true,
      data: {
        managedBots: ownedResult.data.bots,
        friendBots: friendsResult.ok ? friendsResult.data.items.map(toConversationFriendBotView) : [],
        hasAgentCodingBots: ownedResult.data.hasAgentCodingBots,
      },
    };
  },

  /** 管理 Bot(origin=mine)会话分页;scope=favorite 走收藏会话接口。 */
  async listManagedSessions(
    bot: ChatBotView,
    userId: string,
    scope: ConversationSessionScope,
    page: number = 1,
    pageSize: number = BOT_SESSION_PAGE_SIZE,
  ): Promise<DomainResult<BotSessionPageView>> {
    if (scope === 'favorite' && supportsBotSessionFavorites(bot.engine)) {
      return botSessionService.listFavoriteSessionsPage(bot, userId, page, pageSize);
    }
    const result = await botSessionService.listSessionsPage(bot, userId, page, pageSize);
    return result.ok ? { ok: true, data: await conversationFavoriteService.hydrate(bot, userId, result.data) } : result;
  },

  /** Human→Bot 好友 Bot 会话分页:鉴权 Human 既作 user_id 也作 f_user_id。 */
  async listFriendBotSessions(
    bot: ChatBotView,
    userId: string,
    page: number = 1,
    pageSize: number = BOT_SESSION_PAGE_SIZE,
  ): Promise<DomainResult<BotSessionPageView>> {
    const friendBot = { ...bot, isFriendBot: true };
    const result = await botSessionService.listSessionsPage(friendBot, userId, page, pageSize);
    return result.ok
      ? { ok: true, data: await conversationFavoriteService.hydrate(friendBot, userId, result.data) }
      : result;
  },
};
