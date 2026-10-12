// 会话列表 Hook:管理/团队 Bot(origin=mine)与好友 Bot 会话；managedBots 参数包含管理和团队目录。
// origin=others 由 useManagedBotOthers 负责；列表首拉/分页与写操作互斥。
// 缓存态拼装/写复用 ./conversationSessionCache。
import type {
  ConversationBotSection,
  ConversationBotView,
  ConversationSessionScope,
} from '@/domain/conversation/types';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { conversationService } from '@/services/workspace/conversationService';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  firstPageOf,
  nextPageOf,
  requestKeyOf,
  sessionRequestPage,
  writeSessionState,
} from './conversationSessionCache';
import { useConversationBotExpansion } from './useConversationBotExpansion';
import { useConversationFavorites } from './useConversationFavorites';
import { useConversationFavoriteScope } from './useConversationFavoriteScope';
import { useConversationSessionActions } from './useConversationSessionActions';
import { useConversationSessionCreation } from './useConversationSessionCreation';
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
  const actions = useConversationSessionActions(options);
  const expandedBotIds = useConversationStore((s) => s.expandedBotIds);
  const originByManagedBotId = useConversationStore((s) => s.originByManagedBotId);
  const effectiveScopeByManagedBotId = useConversationStore((s) => s.effectiveScopeByManagedBotId);
  const botByBotId = useMemo(() => {
    const map = new Map<string, ConversationBotView>();
    for (const view of [...managedBots, ...friendBots]) map.set(view.bot.botId, view);
    return map;
  }, [managedBots, friendBots]);
  const expandBot = useConversationBotExpansion(userId);
  const createSession = useConversationSessionCreation(userId, botByBotId);
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
  const loadPage = useRef((view: ConversationBotView, scope: ConversationSessionScope, more: boolean): void => {
    const botId = view.bot.botId;
    const sectionOf: ConversationBotSection = view.section;
    const userIdNow = userIdRef.current;
    if (!userIdNow || !view.bot.chatable || view.bot.isAgentCodingBot || actions.isPending(botId, sectionOf)) return;
    const store = useConversationStore.getState();
    const managedCache = isManagedConversationSection(sectionOf) ? store.sessionsByBotId[botId] : undefined;
    const state = isManagedConversationSection(sectionOf)
      ? managedCache?.sessions
      : store.friendBotSessionsByBotId[botId];
    const requestKey = requestKeyOf(botId, sectionOf, scope, more);
    if (inFlightRef.current.has(requestKey)) return;
    if (more && (!state || !state.hasMore || state.loading || state.isLoadingMore)) return;
    const cachedHit = isManagedConversationSection(sectionOf)
      ? managedCache?.origin === 'mine' && managedCache?.scope === scope && !managedCache?.sessions.error
      : Boolean(state && !state.error);
    if (!more && cachedHit && !(state?.hasMore && state.items.length === 0)) return;
    if (!more && isManagedConversationSection(sectionOf) && (store.originByManagedBotId[botId] ?? 'mine') !== 'mine')
      return;
    inFlightRef.current.add(requestKey);
    const generation = generationOf();
    const page = sessionRequestPage(state, scope, more);
    writeSessionState(botId, sectionOf, scope, {
      ...(state ?? firstPageOf([], 0)),
      ...(more ? { isLoadingMore: true, loadMoreError: null } : { loading: true, error: null }),
    });
    void (
      isManagedConversationSection(sectionOf)
        ? conversationService.listManagedSessions(view.bot, userIdNow, scope, page)
        : conversationService.listFriendBotSessions(view.bot, userIdNow, page)
    ).then((result) => {
      inFlightRef.current.delete(requestKey);
      if (generationOf() !== generation) return;
      if (isManagedConversationSection(sectionOf) && !stillMine(botId, scope)) return;
      const latest = isManagedConversationSection(sectionOf)
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
      isManagedConversationSection(view.section) &&
      (useConversationStore.getState().originByManagedBotId[botId] ?? 'mine') !== 'mine'
    ) {
      return;
    }
    loadPage(view, isManagedConversationSection(view.section) ? effectiveScopeOf(botId) : 'all', false);
  }).current;
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
  const pendingActions = [...managedBots, ...friendBots]
    .map((view) => (actions.isPending(view.bot.botId, view.section) ? view.bot.botId : ''))
    .join('|');
  useEffect(() => {
    if (!userId) return;
    for (const botId of Object.keys(expandedBotIds)) {
      const view = botByBotId.get(botId);
      if (view) ensureViewLoaded(view);
    }
  }, [botByBotId, ensureViewLoaded, expandedBotIds, scopeSignature, userId, pendingActions]);
  const toggleBot = useCallback(
    (botId: string, section: ConversationBotSection): void => {
      const view = botByBotId.get(botId);
      if (!view || view.section !== section) return;
      const store = useConversationStore.getState();
      if (store.expandedBotIds[botId]) store.setExpandedBot(botId, false);
      else {
        expandBot(view);
        ensureViewLoaded(view);
      }
    },
    [botByBotId, ensureViewLoaded, expandBot],
  );
  const selectSession = useCallback((section: ConversationBotSection, botId: string, sessionId: string): void => {
    const store = useConversationStore.getState();
    if (!store.expandedBotIds[botId]) store.setExpandedBot(botId, true);
    store.selectConversation({
      botId,
      section,
      origin: 'mine',
      scope: isManagedConversationSection(section) ? effectiveScopeOf(botId) : 'all',
      friendUserId: null,
      sessionId,
    });
  }, []);
  const loadMoreSessions = useCallback(
    async (botId: string, section: ConversationBotSection, scope: ConversationSessionScope): Promise<void> => {
      const view = botByBotId.get(botId);
      if (!view || view.section !== section) return;
      if (isManagedConversationSection(section)) {
        const cached = useConversationStore.getState().sessionsByBotId[botId];
        if (!cached || cached.origin !== 'mine' || cached.scope !== scope) return;
      }
      loadPage(view, scope, true);
    },
    [botByBotId, loadPage],
  );
  const retrySessions = useCallback(
    (botId: string, section: ConversationBotSection) => {
      const view = botByBotId.get(botId);
      if (!view || view.section !== section) return;
      expandBot(view);
      ensureViewLoaded(view);
    },
    [botByBotId, ensureViewLoaded, expandBot],
  );
  return {
    actions,
    favorites,
    openBotIds: expandedBotIds,
    toggleBot,
    selectMineSession: (botId, sessionId) =>
      selectSession(botByBotId.get(botId)?.section ?? 'managed', botId, sessionId),
    selectFriendBotSession: (botId, sessionId) => selectSession('friend', botId, sessionId),
    createSession,
    retrySessions,
    loadMoreSessions,
  };
}
