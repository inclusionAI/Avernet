import { appendUnique } from '@/services/workspace/botSessionHelpers';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { BOT_SESSION_PAGE_SIZE, botSessionService } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useRef, useState } from 'react';

import type { BotSessionPageMeta, UseBotSessionMapResult } from './useBotSessionMap.types';
import { errorBotPageMeta, hasMoreForPage, successBotPageMeta } from './useBotSessionMap.utils';
import { useExpandedBotLoader } from './useExpandedBotLoader';
export type { BotSessionPageMeta, UseBotSessionMapResult } from './useBotSessionMap.types';

/** 以 botId 键控缓存各 bot 会话；首屏及追加均使用 10 条，身份切换清缓存。 */
export function useBotSessionMap(
  chatBots: ChatBotView[],
  expandedBotIds: string[],
  activeIdentityId: string | null,
): UseBotSessionMapResult {
  const [rawByBotId, setRawByBotId] = useState<Record<string, BotChatSessionView[]>>({});
  const [favoriteByBotId, setFavoriteByBotId] = useState<Record<string, BotChatSessionView[]>>({});
  const [pageMetaByBotId, setPageMetaByBotId] = useState<Record<string, BotSessionPageMeta>>({});
  const [favoritePageMetaByBotId, setFavoritePageMetaByBotId] = useState<Record<string, BotSessionPageMeta>>({});
  const [isLoading, setIsLoading] = useState(false);
  const inFlightRef = useRef<Set<string>>(new Set());
  const loadedRef = useRef<Set<string>>(new Set());
  const favoriteLoadedRef = useRef<Set<string>>(new Set());
  const generationRef = useRef(0);

  // 身份切换 → 渲染期同步重置（镜像 useSessionMap）：useEffect 在 commit 后才清空，
  // 会让首帧先绘制旧身份的会话数据再变骨架（闪烁）；generation 使旧身份在途请求回填失效。
  const [lastIdentityId, setLastIdentityId] = useState(activeIdentityId);
  if (lastIdentityId !== activeIdentityId) {
    setLastIdentityId(activeIdentityId);
    generationRef.current += 1;
    inFlightRef.current.clear();
    loadedRef.current.clear();
    favoriteLoadedRef.current.clear();
    setRawByBotId({});
    setFavoriteByBotId({});
    setPageMetaByBotId({});
    setFavoritePageMetaByBotId({});
    setIsLoading(false);
  }

  const syncLoadingState = useCallback(() => setIsLoading(inFlightRef.current.size > 0), []);

  const loadFirstPage = useCallback(
    async (bot: ChatBotView, userId: string): Promise<void> => {
      const generation = generationRef.current;
      const key = bot.botId;
      const requestKey = `${key}:all`;
      if (inFlightRef.current.has(requestKey)) return;
      inFlightRef.current.add(requestKey);
      syncLoadingState();
      let loaded = false;
      try {
        const res = await botSessionService.listSessionsPage(bot, userId, 1, BOT_SESSION_PAGE_SIZE);
        if (generation !== generationRef.current) return;
        if (res.ok) {
          loaded = true;
          setRawByBotId((current) => ({ ...current, [key]: res.data.items }));
          setPageMetaByBotId((current) => ({
            ...current,
            [key]: successBotPageMeta(res.data.total, hasMoreForPage(res.data.items.length, res.data.total, 1), 2),
          }));
        } else {
          setPageMetaByBotId((current) => ({
            ...current,
            [key]: errorBotPageMeta(current[key], res.error.friendlyMessage),
          }));
        }
      } finally {
        inFlightRef.current.delete(requestKey);
        if (generation === generationRef.current) {
          if (loaded) loadedRef.current.add(key);
          syncLoadingState();
        }
      }
    },
    [syncLoadingState],
  );

  // 展开 transition 必重拉最新会话列表；持续展开按 loadedRef 去重（实现见 useExpandedBotLoader）。
  useExpandedBotLoader(chatBots, expandedBotIds, activeIdentityId, loadFirstPage, loadedRef);

  const updateBotSessions = useCallback(
    (botId: string, fn: (list: BotChatSessionView[]) => BotChatSessionView[]) =>
      setRawByBotId((current) => ({ ...current, [botId]: fn(current[botId] ?? []) })),
    [],
  );

  const updateBotFavoriteSessions = useCallback(
    (botId: string, fn: (list: BotChatSessionView[]) => BotChatSessionView[]) =>
      setFavoriteByBotId((current) => ({ ...current, [botId]: fn(current[botId] ?? []) })),
    [],
  );

  const reloadBot = useCallback(
    async (bot: ChatBotView, userId: string): Promise<void> => {
      loadedRef.current.delete(bot.botId);
      inFlightRef.current.delete(`${bot.botId}:all`);
      favoriteLoadedRef.current.delete(bot.botId);
      setFavoriteByBotId((current) => ({ ...current, [bot.botId]: [] }));
      setFavoritePageMetaByBotId((current) => {
        const next = { ...current };
        delete next[bot.botId];
        return next;
      });
      await loadFirstPage(bot, userId);
    },
    [loadFirstPage],
  );

  const loadFavoriteSessions = useCallback(
    async (bot: ChatBotView, userId: string): Promise<void> => {
      const generation = generationRef.current;
      const key = bot.botId;
      const requestKey = `${key}:favorite`;
      if (favoriteLoadedRef.current.has(key) || inFlightRef.current.has(requestKey)) return;
      inFlightRef.current.add(requestKey);
      syncLoadingState();
      try {
        const res = await botSessionService.listFavoriteSessionsPage(bot, userId, 1, BOT_SESSION_PAGE_SIZE);
        if (generation !== generationRef.current) return;
        if (!res.ok) {
          setFavoritePageMetaByBotId((current) => ({
            ...current,
            [key]: {
              total: current[key]?.total ?? 0,
              hasMore: false,
              nextPage: current[key]?.nextPage ?? 1,
              isLoadingMore: false,
              error: res.error.friendlyMessage,
            },
          }));
          return;
        }
        setFavoriteByBotId((current) => ({ ...current, [key]: res.data.items }));
        setFavoritePageMetaByBotId((current) => ({
          ...current,
          [key]: {
            total: res.data.total,
            hasMore: hasMoreForPage(res.data.items.length, res.data.total, 1),
            nextPage: 2,
            isLoadingMore: false,
            error: undefined,
            loadMoreError: undefined,
          },
        }));
        const favoriteIds = new Set(res.data.items.map((session) => session.sessionId));
        setRawByBotId((current) => ({
          ...current,
          [key]: (current[key] ?? []).map((session) =>
            favoriteIds.has(session.sessionId) ? { ...session, favorite: true } : session,
          ),
        }));
        favoriteLoadedRef.current.add(key);
      } finally {
        inFlightRef.current.delete(requestKey);
        syncLoadingState();
      }
    },
    [syncLoadingState],
  );

  const loadMoreSessions = useCallback(
    async (bot: ChatBotView, userId: string, mode: 'all' | 'favorite') => {
      const key = bot.botId;
      const requestKey = `${key}:${mode}`;
      const meta = mode === 'all' ? pageMetaByBotId[key] : favoritePageMetaByBotId[key];
      if (!meta || !meta.hasMore || meta.isLoadingMore || inFlightRef.current.has(requestKey)) return;
      const generation = generationRef.current;
      const setMeta = mode === 'all' ? setPageMetaByBotId : setFavoritePageMetaByBotId;
      inFlightRef.current.add(requestKey);
      syncLoadingState();
      setMeta((current) => ({ ...current, [key]: { ...current[key], isLoadingMore: true } }));
      try {
        const res =
          mode === 'all'
            ? await botSessionService.listSessionsPage(bot, userId, meta.nextPage, BOT_SESSION_PAGE_SIZE)
            : await botSessionService.listFavoriteSessionsPage(bot, userId, meta.nextPage, BOT_SESSION_PAGE_SIZE);
        if (generation !== generationRef.current) return;
        if (res.ok) {
          if (mode === 'all') {
            setRawByBotId((current) => ({ ...current, [key]: appendUnique(current[key] ?? [], res.data.items) }));
          } else {
            setFavoriteByBotId((current) => ({ ...current, [key]: appendUnique(current[key] ?? [], res.data.items) }));
          }
          setMeta((current) => ({
            ...current,
            [key]: successBotPageMeta(
              res.data.total,
              hasMoreForPage(res.data.items.length, res.data.total, meta.nextPage),
              meta.nextPage + 1,
            ),
          }));
          if (mode === 'favorite') favoriteLoadedRef.current.add(key);
        } else {
          setMeta((current) => ({
            ...current,
            [key]: { ...current[key], isLoadingMore: false, loadMoreError: res.error.friendlyMessage },
          }));
        }
      } catch {
        if (generation === generationRef.current)
          setMeta((current) => ({
            ...current,
            [key]: { ...current[key], isLoadingMore: false, loadMoreError: '加载更多会话失败，请重试' },
          }));
      } finally {
        inFlightRef.current.delete(requestKey);
        syncLoadingState();
      }
    },
    [favoritePageMetaByBotId, pageMetaByBotId, syncLoadingState],
  );

  const toggleBotExpanded = useCallback((botId: string, sectionKey: string = 'mine') => {
    const store = useWorkspaceStore.getState();
    const willExpand = !store.expandedBotIds[botId] || store.expandedBotSectionKey[botId] !== sectionKey;
    store.toggleBotExpanded(botId);
    if (willExpand) useWorkspaceStore.getState().setBotExpandedSection(botId, sectionKey);
  }, []);

  return {
    rawByBotId,
    favoriteByBotId,
    pageMetaByBotId,
    favoritePageMetaByBotId,
    isLoading,
    updateBotSessions,
    updateBotFavoriteSessions,
    reloadBot,
    loadFavoriteSessions,
    loadMoreSessions,
    toggleBotExpanded,
    setRawByBotId,
    loadedRef,
  };
}
