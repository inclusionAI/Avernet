import { supportsBotSessionFavorites } from '@/domain/botEngine';
import { botSessionService, type BotSessionPageView, type ChatBotView } from './botSessionService';
import type { DomainResult } from './identityService';

const FAVORITE_PAGE_SIZE = 100;

/** 普通会话接口不返回收藏状态，通过收藏分页补全当前可见页，不能仅匹配收藏首页。 */
export const conversationFavoriteService = {
  async hydrate(bot: ChatBotView, userId: string, sessions: BotSessionPageView): Promise<BotSessionPageView> {
    if (!supportsBotSessionFavorites(bot.engine) || sessions.items.length === 0) return sessions;
    const favorites = new Set<string>();
    let page = 1;
    while (true) {
      const result = await botSessionService.listFavoriteSessionsPage(bot, userId, page, FAVORITE_PAGE_SIZE);
      // 普通会话仍可读；未知收藏状态由 UI 禁用星标，避免误发反向操作。
      if (!result.ok) return sessions;
      result.data.items.forEach((item) => favorites.add(item.sessionId));
      if (
        result.data.items.length === 0 ||
        page * FAVORITE_PAGE_SIZE >= result.data.total ||
        sessions.items.every((item) => favorites.has(item.sessionId))
      )
        break;
      page += 1;
    }
    return { ...sessions, items: sessions.items.map((item) => ({ ...item, favorite: favorites.has(item.sessionId) })) };
  },

  async toggle(bot: ChatBotView, userId: string, sessionId: string, favorite: boolean): Promise<DomainResult<boolean>> {
    if (!supportsBotSessionFavorites(bot.engine)) {
      return {
        ok: false,
        error: { code: 'UNSUPPORTED', friendlyMessage: '当前引擎暂不支持会话收藏。', canRetry: false },
      };
    }
    return botSessionService.toggleFavorite(bot, userId, sessionId, favorite);
  },
};
