import type {
  ConversationBotSection,
  ConversationBotView,
  ConversationOrigin,
  ConversationSessionScope,
} from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { useConversationStore } from '@/stores/conversationStore';

export interface ConversationBotSelectionTarget {
  botId: string;
  section: ConversationBotSection;
  origin: ConversationOrigin;
  scope: ConversationSessionScope;
}

/** 仅返回目标当前来源/范围的列表，不能把上一范围缓存当成已加载。 */
function getList(target: ConversationBotSelectionTarget) {
  if (target.origin !== 'mine') return undefined;
  const state = useConversationStore.getState();
  if (target.section === 'friend') return state.friendBotSessionsByBotId[target.botId];
  const cache = state.sessionsByBotId[target.botId];
  return cache?.origin === 'mine' && cache.scope === target.scope ? cache.sessions : undefined;
}

export const conversationBotSelectionService = {
  getList,
  begin(view: ConversationBotView): ConversationBotSelectionTarget {
    const store = useConversationStore.getState();
    const botId = view.bot.botId;
    const origin = isManagedConversationSection(view.section) ? store.originByManagedBotId[botId] ?? 'mine' : 'mine';
    const scope =
      isManagedConversationSection(view.section) && origin === 'mine'
        ? store.effectiveScopeByManagedBotId[botId] ?? 'all'
        : 'all';
    const target = { botId, section: view.section, origin, scope };
    store.setExpandedBot(botId, true);
    store.selectConversation({ ...target, friendUserId: null, sessionId: null });
    return target;
  },

  /** true = 完成/失效；false = 仍在等待目标列表。显式选择永远优先于自动首选。 */
  complete(target: ConversationBotSelectionTarget): boolean {
    const state = useConversationStore.getState();
    if (
      !state.expandedBotIds[target.botId] ||
      state.selectedBotId !== target.botId ||
      state.selectedSection !== target.section ||
      state.selectedOrigin !== target.origin ||
      state.selectedScope !== target.scope ||
      state.selectedSessionId
    )
      return true;
    if (target.origin === 'mine') {
      const list = getList(target);
      if (!list || list.loading || list.error) return false;
      const first = list.items[0];
      if (first) state.selectConversation({ ...target, friendUserId: null, sessionId: first.sessionId });
      return Boolean(first) || !list.hasMore;
    }
    // 他人视角仍按现有展开好友分组懒加载，不额外遍历/请求所有好友的会话。
    const cache = state.sessionsByBotId[target.botId];
    if (cache?.origin !== 'others' || cache.friendDirectory.loading || cache.friendDirectory.error) return false;
    for (const friend of cache.friendDirectory.items) {
      if (!state.expandedFriendUserIdsByBotId[target.botId]?.[friend.userId]) continue;
      const group = cache.friendGroups[friend.userId];
      if (!group || group.state !== 'loaded' || group.sessions.loading || group.sessions.error) return false;
      const first = group.sessions.items[0];
      if (!first) continue;
      state.selectConversation({ ...target, friendUserId: friend.userId, sessionId: first.sessionId });
      return true;
    }
    return cache.friendDirectory.items.length === 0;
  },

  showAll(botId: string): void {
    useConversationStore.getState().setManagedBotScope(botId, 'all');
  },
};
