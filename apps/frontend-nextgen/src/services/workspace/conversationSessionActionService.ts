import type { ConversationBotSection, ConversationSessionListState } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { useConversationStore } from '@/stores/conversationStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { BOT_SESSION_PAGE_SIZE, botSessionService, type ChatBotView } from './botSessionService';
import type { DomainResult } from './identityService';

export type ConversationSessionAction = { type: 'rename'; title: string } | { type: 'clear' | 'delete' };
export interface ConversationSessionActionTarget {
  bot: ChatBotView;
  section: ConversationBotSection;
  userId: string;
  sessionId: string;
}

function failure(message: string): DomainResult<never> {
  return { ok: false, error: { code: 'SESSION_ACTION_UNAVAILABLE', friendlyMessage: message, canRetry: false } };
}

/** 当前可写列表：绝不把别人发起的会话当成当前用户的会话。 */
function currentList(botId: string, section: ConversationBotSection): ConversationSessionListState | undefined {
  const store = useConversationStore.getState();
  if (section === 'friend') return store.friendBotSessionsByBotId[botId];
  const cache = store.sessionsByBotId[botId];
  if ((store.originByManagedBotId[botId] ?? 'mine') !== 'mine' || cache?.origin !== 'mine') return undefined;
  if (cache.scope !== (store.effectiveScopeByManagedBotId[botId] ?? 'all')) return undefined;
  return cache.sessions;
}

export const conversationSessionActionService = {
  currentList,
  async execute(
    target: ConversationSessionActionTarget & { action: ConversationSessionAction },
  ): Promise<DomainResult<ConversationSessionAction>> {
    const { bot, section, userId, sessionId, action } = target;
    const list = currentList(bot.botId, section);
    const session = list?.items.find((item) => item.sessionId === sessionId);
    if (!userId || !bot.chatable || bot.isAgentCodingBot || !session || session.botId !== bot.botId) {
      return failure('当前会话不可操作，请重新加载会话列表。');
    }
    if (list?.loading || list?.isLoadingMore) return failure('会话列表加载中，请稍后重试。');
    if (action.type === 'rename') {
      const title = action.title.trim();
      if (!title) return failure('会话标题不能为空。');
      if (title === session.title) return { ok: true, data: { type: 'rename', title } };
      const result = await botSessionService.updateSessionTitle(bot, userId, sessionId, title);
      return result.ok ? { ok: true, data: { type: 'rename', title: result.data.title } } : result;
    }
    const result =
      action.type === 'clear'
        ? await botSessionService.clearContext(bot, userId, sessionId)
        : await botSessionService.deleteSession(bot, userId, sessionId);
    return result.ok ? { ok: true, data: action } : result;
  },

  /** Hook 已确认仍为当前用户后调用；只更新最新可写缓存，不拿请求前快照覆盖收藏/模型等并发更新。 */
  apply(target: ConversationSessionActionTarget, action: ConversationSessionAction): void {
    const { bot, section, sessionId } = target;
    const store = useConversationStore.getState();
    const list = currentList(bot.botId, section);
    if (!list?.items.some((item) => item.sessionId === sessionId)) return;
    const items =
      action.type === 'delete'
        ? list.items.filter((item) => item.sessionId !== sessionId)
        : list.items.map((item) =>
            item.sessionId !== sessionId
              ? item
              : {
                  ...item,
                  ...(action.type === 'rename' ? { title: action.title } : { messageCount: 0 }),
                },
          );
    const total = action.type === 'delete' ? Math.max(items.length, list.total - 1) : list.total;
    const next = {
      ...list,
      items,
      total,
      // 删除会让后端 offset 前移；下次重取不足的那一页，nextPageOf 会去重，避免跳过补位项。
      ...(action.type === 'delete'
        ? { hasMore: items.length < total, page: Math.floor(items.length / BOT_SESSION_PAGE_SIZE) }
        : {}),
    };
    if (isManagedConversationSection(section))
      store.setManagedBotCache(bot.botId, { ...store.sessionsByBotId[bot.botId], sessions: next });
    else store.setFriendBotSessions(bot.botId, next);
    const selected =
      store.selectedBotId === bot.botId &&
      store.selectedSection === section &&
      store.selectedOrigin === 'mine' &&
      store.selectedSessionId === sessionId;
    if (selected && action.type === 'clear') useWorkspaceStore.getState().bumpHistoryRefresh();
    if (selected && action.type === 'delete')
      store.selectConversation({
        botId: bot.botId,
        section,
        origin: 'mine',
        scope: store.selectedScope,
        friendUserId: null,
        sessionId: items[0]?.sessionId ?? null,
      });
  },
};
