import type { ConversationBotView, ConversationOrigin, ConversationSessionRequestRange } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import type { BotSessionPageView } from '@/services/workspace/botSessionService';
import { conversationFavoriteService } from '@/services/workspace/conversationFavoriteService';
import { useConversationStore } from '@/stores/conversationStore';
import { writeSessionState } from './conversationSessionCache';

/** 列表先写回，收藏后台补齐；不更新 total/page/选中态，不覆盖已知或刚写入的星标。 */
export async function hydrateConversationFavorites(
  view: ConversationBotView,
  userId: string,
  page: BotSessionPageView,
  origin: ConversationOrigin,
  isCurrent: () => boolean,
  range?: ConversationSessionRequestRange,
): Promise<void> {
  if (!page.items.some((item) => item.favorite === undefined)) return;
  try {
    const hydrated = await conversationFavoriteService.hydrate(view.bot, userId, page, range);
    if (!isCurrent()) return;
    const store = useConversationStore.getState();
    const current = isManagedConversationSection(view.section)
      ? store.sessionsByBotId[view.bot.botId]?.sessions
      : store.friendBotSessionsByBotId[view.bot.botId];
    if (!current) return;
    const flags = new Map(hydrated.items.map((item) => [item.sessionId, item.favorite]));
    let changed = false;
    const items = current.items.map((item) => {
      const favorite = flags.get(item.sessionId);
      if (item.favorite !== undefined || favorite === undefined) return item;
      changed = true;
      return { ...item, favorite };
    });
    if (changed) writeSessionState(view.bot.botId, view.section, 'all', { ...current, items }, origin);
  } catch {
    // 收藏补全失败不变成列表错误，未知星标保持 disabled；后续重载可以重试。
  }
}
