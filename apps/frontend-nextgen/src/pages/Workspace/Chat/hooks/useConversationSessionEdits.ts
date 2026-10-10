// 会话页会话编辑三操作 Hook:首条消息自动重命名、清除上下文、模型切换。
// 行为对齐旧 useBotSessions 的同名操作(服务调用 + 缓存回写 + toast),
// 但缓存读写收口到 conversationStore(对话页唯一会话缓存),避免旧
// useBotSessionMap 对展开 Bot 的并行整拉与旧 workspaceStore 选中态旁路。
// 三操作契约见 useConversationInteractiveChat 的 ConversationInteractiveSessionOperations。
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { buildFirstMessageSessionTitle } from '@/services/workspace/botSessionTitleService';
import { useConversationStore } from '@/stores/conversationStore';
import type { ConversationSessionListState } from '@/stores/conversationStoreState';
import { useCallback, useRef } from 'react';
import { toast } from 'sonner';
import type { ConversationInteractiveSessionOperations } from './useConversationInteractiveChat';

/** 对指定 Bot 的全部会话列表缓存(管理 mine / 好友 Bot)套用单会话补丁。 */
function patchSessionLists(
  botId: string,
  sessionId: string,
  patch: (session: BotChatSessionView) => BotChatSessionView,
): void {
  const store = useConversationStore.getState();
  const patchList = (list: ConversationSessionListState): ConversationSessionListState => {
    if (!list.items.some((item) => item.sessionId === sessionId)) return list;
    return {
      ...list,
      items: list.items.map((item) => (item.sessionId === sessionId ? patch(item) : item)),
    };
  };
  const managed = store.sessionsByBotId[botId];
  if (managed && managed.origin === 'mine') {
    const sessions = patchList(managed.sessions);
    if (sessions !== managed.sessions) store.setManagedBotCache(botId, { ...managed, sessions });
  }
  const friend = store.friendBotSessionsByBotId[botId];
  if (friend) {
    const sessions = patchList(friend);
    if (sessions !== friend) store.setFriendBotSessions(botId, sessions);
  }
}

export function useConversationSessionEdits(userId: string | null): ConversationInteractiveSessionOperations {
  // 首条消息只尝试一次自动命名(与旧 useBotSessionTitleActions 同构)。
  const attemptedFirstMessageTitlesRef = useRef<Set<string>>(new Set());

  const renameSessionOnFirstMessage = useCallback(
    async (bot: ChatBotView, session: BotChatSessionView, content: string): Promise<boolean> => {
      if (!userId) return false;
      const title = buildFirstMessageSessionTitle(session, content);
      if (!title) return false;
      const attemptKey = `${userId}:${bot.botId}:${session.sessionId}`;
      if (attemptedFirstMessageTitlesRef.current.has(attemptKey)) return false;
      attemptedFirstMessageTitlesRef.current.add(attemptKey);
      const res = await botSessionService.updateSessionTitle(bot, userId, session.sessionId, title);
      if (!res.ok) {
        console.warn('[useConversationSessionEdits] 首条消息自动重命名失败:', res.error.friendlyMessage);
        return false;
      }
      patchSessionLists(bot.botId, session.sessionId, (item) => ({ ...item, title: res.data.title }));
      return true;
    },
    [userId],
  );

  const clearContext = useCallback(
    async (bot: ChatBotView, sessionId: string): Promise<boolean> => {
      if (!userId) return false;
      const res = await botSessionService.clearContext(bot, userId, sessionId);
      if (!res.ok) {
        toast.error(res.error.friendlyMessage);
        return false;
      }
      patchSessionLists(bot.botId, sessionId, (item) => ({ ...item, messageCount: 0 }));
      toast.success('会话上下文已清除');
      return true;
    },
    [userId],
  );

  const updateSessionModel = useCallback((botId: string, sessionId: string, model: string): void => {
    patchSessionLists(botId, sessionId, (item) => ({ ...item, model }));
  }, []);

  return { renameSessionOnFirstMessage, clearContext, updateSessionModel };
}
