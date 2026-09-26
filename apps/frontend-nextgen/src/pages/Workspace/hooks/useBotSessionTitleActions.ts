import { botSessionService, type BotChatSessionView, type ChatBotView } from '@/services/workspace/botSessionService';
import { buildFirstMessageSessionTitle } from '@/services/workspace/botSessionTitleService';
import { useCallback, useRef } from 'react';
import { toast } from 'sonner';
import type { UseBotSessionMapResult } from './useBotSessionMap.types';

type UpdateSessions = UseBotSessionMapResult['updateBotSessions'];

/** 单聊标题操作：手动重命名 + 首条消息自动命名，共用列表缓存同步。 */
export function useBotSessionTitleActions(
  activeIdentityId: string | null,
  updateBotSessions: UpdateSessions,
  updateBotFavoriteSessions: UpdateSessions,
) {
  const attemptedFirstMessageTitlesRef = useRef<Set<string>>(new Set());

  const applyTitle = useCallback(
    (botId: string, sessionId: string, title: string) => {
      const patch = (list: BotChatSessionView[]) =>
        list.map((session) => (session.sessionId === sessionId ? { ...session, title } : session));
      updateBotSessions(botId, patch);
      updateBotFavoriteSessions(botId, patch);
    },
    [updateBotFavoriteSessions, updateBotSessions],
  );

  const renameSession = useCallback(
    async (bot: ChatBotView, sessionId: string, title: string): Promise<boolean> => {
      if (!activeIdentityId) return false;
      const res = await botSessionService.updateSessionTitle(bot, activeIdentityId, sessionId, title);
      if (!res.ok) {
        toast.error(res.error.friendlyMessage);
        return false;
      }
      applyTitle(bot.botId, sessionId, res.data.title);
      toast.success('会话已重命名');
      return true;
    },
    [activeIdentityId, applyTitle],
  );

  const renameSessionOnFirstMessage = useCallback(
    async (bot: ChatBotView, session: BotChatSessionView, content: string): Promise<boolean> => {
      if (!activeIdentityId) return false;
      const title = buildFirstMessageSessionTitle(session, content);
      if (!title) return false;

      const attemptKey = `${activeIdentityId}:${bot.botId}:${session.sessionId}`;
      if (attemptedFirstMessageTitlesRef.current.has(attemptKey)) return false;
      attemptedFirstMessageTitlesRef.current.add(attemptKey);

      const res = await botSessionService.updateSessionTitle(bot, activeIdentityId, session.sessionId, title);
      if (!res.ok) {
        console.warn('[useBotSessionTitleActions] 首条消息自动重命名失败:', res.error.friendlyMessage);
        return false;
      }
      applyTitle(bot.botId, session.sessionId, res.data.title);
      return true;
    },
    [activeIdentityId, applyTitle],
  );

  return { renameSession, renameSessionOnFirstMessage };
}
