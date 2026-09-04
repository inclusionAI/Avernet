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
  favoriteSessionsByBotId: Record<string, BotChatSessionView[]>;
  sessionPageMetaByBotId: ReturnType<typeof useBotSessionMap>['pageMetaByBotId'];
  favoriteSessionPageMetaByBotId: ReturnType<typeof useBotSessionMap>['favoritePageMetaByBotId'];
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
  loadMoreSessions: (botId: string, mode: 'all' | 'favorite') => Promise<void>;
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
    favoriteByBotId,
    pageMetaByBotId,
    favoritePageMetaByBotId,
    isLoading,
    updateBotSessions,
    updateBotFavoriteSessions,
    reloadBot,
    loadFavoriteSessions: loadFavoriteSessionsFromMap,
    loadMoreSessions: loadMoreSessionsFromMap,
    toggleBotExpanded: toggleBotExpandedFromMap,
  } = useBotSessionMap(chatBots, expandedBotIds, activeIdentityId);
  const selectedBotId = expandedBotIds[0] ?? null;
  const selectedSession = useMemo(() => {
    if (!selectedBotId || !selectedBotSessionId) return null;
    return rawByBotId[selectedBotId]?.find((s) => s.sessionId === selectedBotSessionId) ?? null;
  }, [selectedBotId, rawByBotId, selectedBotSessionId]);
  const selectSession = useCallback((id: string | null) => selectBotSession(id), [selectBotSession]);
  const openSession = useCallback(
    (botId: string, sessionId: string) => {
      // 确保 bot 已展开
      if (!useWorkspaceStore.getState().expandedBotIds[botId]) toggleBotExpandedFromMap(botId);
      selectBotSession(sessionId);
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
      updateBotFavoriteSessions(bot.botId, (list) => list.filter((s) => s.sessionId !== sessionId));
      toast.success('会话已删除');
      return true;
    },
    [activeIdentityId, updateBotFavoriteSessions, updateBotSessions, selectBotSession],
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
      updateBotFavoriteSessions(bot.botId, (list) =>
        list.map((session) => (session.sessionId === sessionId ? { ...session, title: res.data.title } : session)),
      );
      toast.success('会话已重命名');
      return true;
    },
    [activeIdentityId, updateBotFavoriteSessions, updateBotSessions],
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
      updateBotFavoriteSessions(bot.botId, (list) =>
        list.map((session) => (session.sessionId === sessionId ? { ...session, messageCount: 0 } : session)),
      );
      if (useWorkspaceStore.getState().selectedBotSessionId === sessionId) {
        useWorkspaceStore.getState().bumpHistoryRefresh();
      }
      toast.success('会话上下文已清除');
      return true;
    },
    [activeIdentityId, updateBotFavoriteSessions, updateBotSessions],
  );

  const toggleFavorite = useCallback(
    async (botId: string, sessionId: string): Promise<boolean> => {
      if (!activeIdentityId) return false;
      const bot = chatBots.find((b) => b.botId === botId);
      if (!bot) return false;
      const current =
        favoriteByBotId[botId]?.find((session) => session.sessionId === sessionId) ??
        rawByBotId[botId]?.find((session) => session.sessionId === sessionId);
      const fav = current ? !current.favorite : true;
      const res = await botSessionService.toggleFavorite(bot, activeIdentityId, sessionId, fav);
      if (!res.ok) {
        toast.error(res.error.friendlyMessage);
        return false;
      }
      updateBotSessions(botId, (list) =>
        list.map((s) => (s.sessionId === sessionId ? { ...s, favorite: res.data } : s)),
      );
      if (favoriteByBotId[botId] !== undefined) {
        updateBotFavoriteSessions(botId, (list) => {
          if (!res.data) return list.filter((session) => session.sessionId !== sessionId);
          if (list.some((session) => session.sessionId === sessionId)) {
            return list.map((session) => (session.sessionId === sessionId ? { ...session, favorite: true } : session));
          }
          return current ? [{ ...current, favorite: true }, ...list] : list;
        });
      }
      return true;
    },
    [activeIdentityId, chatBots, favoriteByBotId, rawByBotId, updateBotFavoriteSessions, updateBotSessions],
  );
  const loadFavoriteSessions = useCallback(
    async (botId: string): Promise<void> => {
      if (!activeIdentityId) return;
      const bot = chatBots.find((b) => b.botId === botId);
      if (!bot) return;
      await loadFavoriteSessionsFromMap(bot, activeIdentityId);
    },
    [activeIdentityId, chatBots, loadFavoriteSessionsFromMap],
  );

  const loadMoreSessions = useCallback(
    (botId: string, mode: 'all' | 'favorite'): Promise<void> => {
      if (!activeIdentityId) return Promise.resolve();
      const bot = chatBots.find((b) => b.botId === botId);
      return bot ? loadMoreSessionsFromMap(bot, activeIdentityId, mode) : Promise.resolve();
    },
    [activeIdentityId, chatBots, loadMoreSessionsFromMap],
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
    favoriteSessionsByBotId: favoriteByBotId,
    sessionPageMetaByBotId: pageMetaByBotId,
    favoriteSessionPageMetaByBotId: favoritePageMetaByBotId,
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
    loadMoreSessions,
    updateSessionModel,
    reloadBot: (botId: string): Promise<void> => {
      const bot = chatBots.find((b) => b.botId === botId);
      if (bot && activeIdentityId) return reloadBot(bot, activeIdentityId);
      return Promise.resolve();
    },
    toggleBotExpanded: toggleBotExpandedFromMap,
  };
}
