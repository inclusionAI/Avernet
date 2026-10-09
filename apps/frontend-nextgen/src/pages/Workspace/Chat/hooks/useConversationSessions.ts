// 会话列表 Hook:管理 Bot(origin=mine)/ 好友 Bot 会话的懒加载、分页与交互。
// 约定:origin=others 的管理 Bot 会话由 useManagedBotOthers 负责(本 Hook 不加载、不写其缓存);
// 首拉只由展开的 Bot 触发;失败列表不自动重试(重新展开或切换筛选即重试);
// 并发保护:用户切换全局 generation + 按请求 key 去重;写回前校验 origin/scope 仍一致。
// 缓存态拼装/写复用 ./conversationSessionCache。
import type {
  ConversationBotSection,
  ConversationBotView,
  ConversationSessionScope,
} from '@/domain/conversation/types';
import { botSessionService } from '@/services/workspace/botSessionService';
import { conversationService } from '@/services/workspace/conversationService';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { toast } from 'sonner';
import {
  firstPageOf,
  nextPageOf,
  requestKeyOf,
  sessionRequestPage,
  writeSessionState,
} from './conversationSessionCache';
import { useConversationFavoriteScope } from './useConversationFavoriteScope';
import { useConversationFavorites } from './useConversationFavorites';
import type { ConversationSessionsModel } from './useConversationSessions.types';

export type { ConversationSessionsModel } from './useConversationSessions.types';

export function useConversationSessions(options: {
  userId: string | null;
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
}): ConversationSessionsModel {
  const { userId, managedBots, friendBots } = options;
  useConversationFavoriteScope(managedBots);
  const favorites = useConversationFavorites(options);
  const expandedBotIds = useConversationStore((s) => s.expandedBotIds);
  const originByManagedBotId = useConversationStore((s) => s.originByManagedBotId);
  const effectiveScopeByManagedBotId = useConversationStore((s) => s.effectiveScopeByManagedBotId);
  const botByBotId = useMemo(() => {
    const map = new Map<string, ConversationBotView>();
    for (const view of [...managedBots, ...friendBots]) map.set(view.bot.botId, view);
    return map;
  }, [managedBots, friendBots]);
  const generationRef = useRef(0);
  const userIdRef = useRef(userId);
  userIdRef.current = userId;
  const inFlightRef = useRef(new Set<string>());
  useEffect(() => {
    generationRef.current += 1; // 用户切换:在途旧响应整体作废。
    inFlightRef.current.clear();
  }, [userId]);
  useEffect(
    () => () => {
      generationRef.current += 1;
    },
    [],
  );
  const generationOf = (): number => generationRef.current;
  const effectiveScopeOf = (botId: string): ConversationSessionScope =>
    useConversationStore.getState().effectiveScopeByManagedBotId[botId] ?? 'all';
  /** 管理会话写回时读取上下文(origin/scope)须仍一致,不一致即丢弃。 */
  const stillMine = (botId: string, scope: ConversationSessionScope): boolean => {
    const store = useConversationStore.getState();
    return (
      (store.originByManagedBotId[botId] ?? 'mine') === 'mine' &&
      (store.effectiveScopeByManagedBotId[botId] ?? 'all') === scope
    );
  };
  /** 首页与下一页共用分页加载;首页由展开触发,下一页由 loadMoreSessions 触发。 */
  const loadPage = useRef((view: ConversationBotView, scope: ConversationSessionScope, more: boolean): void => {
    const botId = view.bot.botId;
    const sectionOf: ConversationBotSection = view.section;
    const userIdNow = userIdRef.current;
    if (!userIdNow) return;
    const store = useConversationStore.getState();
    const managedCache = sectionOf === 'managed' ? store.sessionsByBotId[botId] : undefined;
    const state = sectionOf === 'managed' ? managedCache?.sessions : store.friendBotSessionsByBotId[botId];
    const requestKey = requestKeyOf(botId, sectionOf, scope, more);
    if (inFlightRef.current.has(requestKey)) return;
    if (more && (!state || !state.hasMore || state.loading || state.isLoadingMore)) return;
    // 失败缓存可重试;其余缓存命中(含在途)不重复首拉。
    const cachedHit =
      sectionOf === 'managed'
        ? managedCache?.origin === 'mine' && managedCache?.scope === scope && !managedCache?.sessions.error
        : Boolean(state && !state.error);
    if (!more && cachedHit) return;
    if (!more && sectionOf === 'managed' && (store.originByManagedBotId[botId] ?? 'mine') !== 'mine') return;
    inFlightRef.current.add(requestKey);
    const generation = generationOf();
    const page = sessionRequestPage(state, scope, more);
    writeSessionState(botId, sectionOf, scope, {
      ...(state ?? firstPageOf([], 0)),
      ...(more ? { isLoadingMore: true, loadMoreError: null } : { loading: true, error: null }),
    });
    void (
      sectionOf === 'managed'
        ? conversationService.listManagedSessions(view.bot, userIdNow, scope, page)
        : conversationService.listFriendBotSessions(view.bot, userIdNow, page)
    ).then((result) => {
      inFlightRef.current.delete(requestKey);
      if (generationOf() !== generation) return;
      if (sectionOf === 'managed' && !stillMine(botId, scope)) return;
      const latest =
        sectionOf === 'managed'
          ? useConversationStore.getState().sessionsByBotId[botId]?.sessions
          : useConversationStore.getState().friendBotSessionsByBotId[botId];
      if (!result.ok) {
        if (!more) {
          writeSessionState(botId, sectionOf, scope, { ...firstPageOf([], 0), error: result.error.friendlyMessage });
        } else if (latest) {
          writeSessionState(botId, sectionOf, scope, nextPageOf(latest, result, page));
        }
        return;
      }
      if (!more) {
        writeSessionState(botId, sectionOf, scope, firstPageOf(result.data.items, result.data.total));
        return;
      }
      if (latest) writeSessionState(botId, sectionOf, scope, nextPageOf(latest, result, page));
    });
  }).current;
  const ensureViewLoaded = useRef((view: ConversationBotView): void => {
    const botId = view.bot.botId;
    // origin=others 的管理 Bot 会话由 useManagedBotOthers 接管,这里不加载。
    if (
      view.section === 'managed' &&
      (useConversationStore.getState().originByManagedBotId[botId] ?? 'mine') !== 'mine'
    ) {
      return;
    }
    loadPage(view, view.section === 'managed' ? effectiveScopeOf(botId) : 'all', false);
  }).current;
  // origin/scope 的任何变化都触发一次「展开即加载」核对;签名串避免把对象塞进依赖。
  const scopeSignature = useMemo(
    () =>
      Object.keys({ ...originByManagedBotId, ...effectiveScopeByManagedBotId })
        .sort()
        .map(
          (botId) =>
            `${botId}:${originByManagedBotId[botId] ?? 'mine'}:${effectiveScopeByManagedBotId[botId] ?? 'all'}`,
        )
        .join('|'),
    [effectiveScopeByManagedBotId, originByManagedBotId],
  );
  useEffect(() => {
    if (!userId) return;
    for (const botId of Object.keys(expandedBotIds)) {
      const view = botByBotId.get(botId);
      if (view) ensureViewLoaded(view);
    }
  }, [botByBotId, ensureViewLoaded, expandedBotIds, scopeSignature, userId]);
  const toggleBot = useCallback(
    (botId: string, section: ConversationBotSection): void => {
      const view = botByBotId.get(botId);
      if (view && view.section !== section) return;
      const store = useConversationStore.getState();
      const expanded = Boolean(store.expandedBotIds[botId]);
      store.setExpandedBot(botId, !expanded);
      if (!expanded && view) ensureViewLoaded(view);
    },
    [botByBotId, ensureViewLoaded],
  );
  const selectSession = useCallback((section: ConversationBotSection, botId: string, sessionId: string): void => {
    const store = useConversationStore.getState();
    if (!store.expandedBotIds[botId]) store.setExpandedBot(botId, true);
    store.selectConversation({
      botId,
      section,
      origin: 'mine',
      scope: section === 'managed' ? effectiveScopeOf(botId) : 'all',
      friendUserId: null,
      sessionId,
    });
  }, []);
  const createSession = useCallback(
    async (botId: string): Promise<void> => {
      const view = botByBotId.get(botId);
      const userIdNow = userIdRef.current;
      if (!userIdNow || !view) return;
      if (
        view.section === 'managed' &&
        (useConversationStore.getState().originByManagedBotId[botId] ?? 'mine') !== 'mine'
      ) {
        return; // others 无交互会话能力。
      }
      const result = await botSessionService.createSession(view.bot, userIdNow); // 复用既有实现,不重复建。
      if (!result.ok) {
        toast.error(result.error.friendlyMessage);
        return;
      }
      const store = useConversationStore.getState();
      const created = result.data;
      const scope = view.section === 'managed' ? effectiveScopeOf(botId) : 'all';
      const managedCache = view.section === 'managed' ? store.sessionsByBotId[botId] : undefined;
      const cachedList =
        managedCache?.sessions ?? (view.section === 'friend' ? store.friendBotSessionsByBotId[botId] : undefined);
      // 仅当缓存即当前读取范围(all)的列表时前置新会话;favorite 列表与
      // 范围不符的缓存交给展开后的重新加载。
      if (
        cachedList &&
        (view.section === 'friend' ||
          (scope === 'all' && managedCache?.origin === 'mine' && managedCache?.scope === scope))
      ) {
        writeSessionState(botId, view.section, scope, {
          ...cachedList,
          items: [
            { ...created, favorite: false },
            ...cachedList.items.filter((item) => item.sessionId !== created.sessionId),
          ],
        });
      }
      if (!store.expandedBotIds[botId]) store.setExpandedBot(botId, true);
      store.selectConversation({
        botId,
        section: view.section,
        origin: 'mine',
        scope,
        friendUserId: null,
        sessionId: created.sessionId,
      });
      toast.success('会话已创建');
    },
    [botByBotId],
  );
  const loadMoreSessions = useCallback(
    async (botId: string, section: ConversationBotSection, scope: ConversationSessionScope): Promise<void> => {
      const view = botByBotId.get(botId);
      if (!view || view.section !== section) return;
      if (section === 'managed') {
        const cached = useConversationStore.getState().sessionsByBotId[botId];
        if (!cached || cached.origin !== 'mine' || cached.scope !== scope) return;
      }
      loadPage(view, scope, true);
    },
    [botByBotId, loadPage],
  );
  return {
    favorites,
    openBotIds: expandedBotIds,
    toggleBot,
    selectMineSession: (botId, sessionId) => selectSession('managed', botId, sessionId),
    selectFriendBotSession: (botId, sessionId) => selectSession('friend', botId, sessionId),
    createSession,
    loadMoreSessions,
  };
}
