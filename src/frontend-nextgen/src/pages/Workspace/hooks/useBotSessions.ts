import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import type { DomainError } from '@/services/workspace/identityService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useMemo } from 'react';
import { toast } from 'sonner';
import { useBotSessionMap } from './useBotSessionMap';

function notifyError(err: DomainError): void {
  toast.error(err.friendlyMessage);
}
function errOf(res: { ok: false; error: DomainError }): DomainError {
  return res.error;
}

export interface UseBotSessionsResult {
  sessionsByBotId: Record<string, BotChatSessionView[]>;
  isSessionsLoading: boolean;
  selectedBotSessionId: string | null;
  selectedSession: BotChatSessionView | null;
  selectSession: (id: string | null) => void;
  openSession: (botId: string, sessionId: string) => void;
  createSession: (bot: ChatBotView, title?: string) => Promise<BotChatSessionView | null>;
  deleteSession: (bot: ChatBotView, sessionId: string) => Promise<boolean>;
  renameSession: (bot: ChatBotView, sessionId: string, title: string) => Promise<boolean>;
  clearContext: (bot: ChatBotView, sessionId: string) => Promise<boolean>;
  toggleFavorite: (botId: string, sessionId: string) => Promise<boolean>;
  loadFavoriteSessions: (botId: string) => Promise<void>;
  updateSessionModel: (botId: string, sessionId: string, model: string) => void;
  reloadBot: (botId: string) => Promise<void>;
  toggleBotExpanded: (botId: string, sectionKey?: string) => void;
}

/** useBotSessions 编排 bot 单聊会话列表:展开懒加载、选中、新建/删除(镜像 useGroupSessions)。 */
export function useBotSessions(
  chatBots: ChatBotView[],
  expandedBotIds: string[],
  activeIdentityId: string | null,
): UseBotSessionsResult {
  const selectedBotSessionId = useWorkspaceStore((s) => s.selectedBotSessionId);
  const selectBotSession = useWorkspaceStore((s) => s.selectBotSession);
  const {
    rawByBotId,
    isLoading,
    updateBotSessions,
    reloadBot,
    toggleBotExpanded: toggleBotExpandedFromMap,
  } = useBotSessionMap(chatBots, expandedBotIds, activeIdentityId);

  const selectedSession = useMemo(() => {
    for (const list of Object.values(rawByBotId)) {
      const found = list.find((s) => s.sessionId === selectedBotSessionId);
      if (found) return found;
    }
    return null;
  }, [rawByBotId, selectedBotSessionId]);

  const selectSession = useCallback((id: string | null) => selectBotSession(id), [selectBotSession]);

  const openSession = useCallback(
    (botId: string, sessionId: string) => {
      // 确保 bot 已展开
      if (!useWorkspaceStore.getState().expandedBotIds[botId]) toggleBotExpandedFromMap(botId);
      selectBotSession(sessionId);
      // 重复点击同一会话也递增 nonce,驱动 useBotChat 重新拉取历史消息。
      useWorkspaceStore.getState().bumpHistoryRefresh();
    },
    [toggleBotExpandedFromMap, selectBotSession],
  );

  const createSession = useCallback(
    async (bot: ChatBotView, title?: string): Promise<BotChatSessionView | null> => {
      if (!activeIdentityId) return null;
      const res = await botSessionService.createSession(bot, activeIdentityId, title);
      if (!res.ok) {
        notifyError(errOf(res));
        return null;
      }
      const created = res.data;
      updateBotSessions(bot.botId, (list) => [created, ...list.filter((s) => s.sessionId !== created.sessionId)]);
      selectBotSession(created.sessionId);
      toast.success('会话已创建');
      const detail = await botSessionService.getSessionDetail(bot, activeIdentityId, created.sessionId);
      if (detail.ok) {
        updateBotSessions(bot.botId, (list) =>
          list.map((session) =>
            session.sessionId === created.sessionId
              ? { ...session, ...detail.data, sessionId: created.sessionId }
              : session,
          ),
        );
        return detail.data;
      }
      return created;
    },
    [activeIdentityId, selectBotSession, updateBotSessions],
  );

  const deleteSession = useCallback(
    async (bot: ChatBotView, sessionId: string): Promise<boolean> => {
      if (!activeIdentityId) return false;
      const res = await botSessionService.deleteSession(bot, activeIdentityId, sessionId);
      if (!res.ok) {
        notifyError(errOf(res));
        return false;
      }
      updateBotSessions(bot.botId, (list) => {
        const next = list.filter((s) => s.sessionId !== sessionId);
        if (useWorkspaceStore.getState().selectedBotSessionId === sessionId) {
          selectBotSession(next[0]?.sessionId ?? null);
        }
        return next;
      });
      toast.success('会话已删除');
      return true;
    },
    [activeIdentityId, updateBotSessions, selectBotSession],
  );

  const renameSession = useCallback(
    async (bot: ChatBotView, sessionId: string, title: string): Promise<boolean> => {
      if (!activeIdentityId) return false;
      const res = await botSessionService.updateSessionTitle(bot, activeIdentityId, sessionId, title);
      if (!res.ok) {
        notifyError(errOf(res));
        return false;
      }
      updateBotSessions(bot.botId, (list) =>
        list.map((session) => (session.sessionId === sessionId ? { ...session, title: res.data.title } : session)),
      );
      toast.success('会话已重命名');
      return true;
    },
    [activeIdentityId, updateBotSessions],
  );

  const clearContext = useCallback(
    async (bot: ChatBotView, sessionId: string): Promise<boolean> => {
      if (!activeIdentityId) return false;
      const res = await botSessionService.clearContext(bot, activeIdentityId, sessionId);
      if (!res.ok) {
        notifyError(errOf(res));
        return false;
      }
      updateBotSessions(bot.botId, (list) =>
        list.map((session) => (session.sessionId === sessionId ? { ...session, messageCount: 0 } : session)),
      );
      if (useWorkspaceStore.getState().selectedBotSessionId === sessionId) {
        useWorkspaceStore.getState().bumpHistoryRefresh();
      }
      toast.success('会话上下文已清除');
      return true;
    },
    [activeIdentityId, updateBotSessions],
  );

  const toggleFavorite = useCallback(
    async (botId: string, sessionId: string): Promise<boolean> => {
      if (!activeIdentityId) return false;
      const bot = chatBots.find((b) => b.botId === botId);
      if (!bot) return false;
      const current =
        useWorkspaceStore.getState().selectedBotSessionId === sessionId
          ? Object.values(rawByBotId)
              .flat()
              .find((s) => s.sessionId === sessionId)
          : null;
      const fav = current ? !current.favorite : true;
      const res = await botSessionService.toggleFavorite(bot, activeIdentityId, sessionId, fav);
      if (!res.ok) {
        toast.error(res.error.friendlyMessage);
        return false;
      }
      updateBotSessions(botId, (list) =>
        list.map((s) => (s.sessionId === sessionId ? { ...s, favorite: res.data } : s)),
      );
      return true;
    },
    [activeIdentityId, chatBots, rawByBotId, updateBotSessions],
  );
  const loadFavoriteSessions = useCallback(
    async (botId: string): Promise<void> => {
      if (!activeIdentityId) return;
      const bot = chatBots.find((b) => b.botId === botId);
      if (!bot) return;
      const res = await botSessionService.listFavoriteSessions(bot, activeIdentityId);
      if (res.ok) {
        // 合并：已有会话更新 favorite 标记，新增的收藏会话追加到列表。
        updateBotSessions(botId, (list) => {
          const favIds = new Set(res.data.map((s) => s.sessionId));
          const updated = list.map((s) => ({ ...s, favorite: favIds.has(s.sessionId) }));
          const existingIds = new Set(list.map((s) => s.sessionId));
          const newOnes = res.data.filter((s) => !existingIds.has(s.sessionId));
          return [...newOnes, ...updated];
        });
      }
    },
    [activeIdentityId, chatBots, updateBotSessions],
  );

  const updateSessionModel = useCallback(
    (botId: string, sessionId: string, model: string) => {
      updateBotSessions(botId, (list) =>
        list.map((session) => (session.sessionId === sessionId ? { ...session, model } : session)),
      );
    },
    [updateBotSessions],
  );

  return {
    sessionsByBotId: rawByBotId,
    isSessionsLoading: isLoading,
    selectedBotSessionId,
    selectedSession,
    selectSession,
    openSession,
    createSession,
    deleteSession,
    renameSession,
    clearContext,
    toggleFavorite,
    loadFavoriteSessions,
    updateSessionModel,
    reloadBot: (botId: string): Promise<void> => {
      const bot = chatBots.find((b) => b.botId === botId);
      if (bot && activeIdentityId) return reloadBot(bot, activeIdentityId);
      return Promise.resolve();
    },
    toggleBotExpanded: toggleBotExpandedFromMap,
  };
}
