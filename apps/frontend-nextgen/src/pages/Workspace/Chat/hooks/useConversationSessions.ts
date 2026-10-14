// 会话列表 Hook:管理/团队 Bot(origin=mine)与好友 Bot 会话；managedBots 参数包含管理和团队目录。
// origin=others 由 useManagedBotOthers 负责；列表首拉/分页与写操作互斥。
// 缓存态拼装/写复用 ./conversationSessionCache;分页请求生命周期(代次/去重/写回校验)
// 拆至 ./conversationSessionPageRequest(文件大小阈值,行为不变)。
import type {
  ConversationBotSection,
  ConversationBotView,
  ConversationSessionScope,
} from '@/domain/conversation/types';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  createSessionRequestContext,
  loadSessionPage,
  type ConversationSessionRequestContext,
} from './conversationSessionPageRequest';
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
  useConversationFavoriteScope(managedBots, friendBots);
  const favorites = useConversationFavorites(options);
  const actions = useConversationSessionActions(options);
  const expandedBotIds = useConversationStore((s) => s.expandedBotIds);
  const originByManagedBotId = useConversationStore((s) => s.originByManagedBotId);
  const effectiveScopeByManagedBotId = useConversationStore((s) => s.effectiveScopeByManagedBotId);
  const scopeByFriendBotId = useConversationStore((s) => s.scopeByFriendBotId);
  const sessionKeyByBotId = useConversationStore((s) => s.sessionKeyByBotId);
  const sessionFilterRevisionByBotId = useConversationStore((s) => s.sessionFilterRevisionByBotId);
  const botByBotId = useMemo(() => {
    const map = new Map<string, ConversationBotView>();
    for (const view of [...managedBots, ...friendBots]) map.set(view.bot.botId, view);
    return map;
  }, [managedBots, friendBots]);
  const expandBot = useConversationBotExpansion(userId);
  const createSession = useConversationSessionCreation(userId, botByBotId);
  // 请求上下文(代次/在途去重)挂载一份;分页请求主体拆见 ./conversationSessionPageRequest。
  const requestCtxRef = useRef<ConversationSessionRequestContext | null>(null);
  if (!requestCtxRef.current) requestCtxRef.current = createSessionRequestContext(userId);
  const requestCtx = requestCtxRef.current;
  requestCtx.userId.current = userId;
  useEffect(() => {
    requestCtx.generation.current += 1; // 用户切换:在途旧响应整体作废。
    requestCtx.inFlight.clear();
  }, [requestCtx, userId]);
  useEffect(
    () => () => {
      requestCtx.generation.current += 1;
    },
    [requestCtx],
  );
  const loadPage = useRef(
    (view: ConversationBotView, more: boolean): void => loadSessionPage(requestCtx, actions.isPending, view, more),
  ).current;
  const ensureViewLoaded = useRef((view: ConversationBotView): void => {
    const botId = view.bot.botId;
    // origin=others 的管理 Bot 会话由 useManagedBotOthers 接管,这里不加载。
    if (
      isManagedConversationSection(view.section) &&
      (useConversationStore.getState().originByManagedBotId[botId] ?? 'mine') !== 'mine'
    ) {
      return;
    }
    loadPage(view, false);
  }).current;
  const scopeSignature = useMemo(
    () =>
      Object.keys({
        ...originByManagedBotId,
        ...effectiveScopeByManagedBotId,
        ...scopeByFriendBotId,
        ...sessionKeyByBotId,
        ...sessionFilterRevisionByBotId,
      })
        .sort()
        .map(
          (botId) =>
            `${botId}:${originByManagedBotId[botId] ?? 'mine'}:${effectiveScopeByManagedBotId[botId] ?? 'all'}:${
              scopeByFriendBotId[botId] ?? 'all'
            }:${sessionKeyByBotId[botId] ?? ''}:${sessionFilterRevisionByBotId[botId] ?? 0}`,
        )
        .join('|'),
    [
      effectiveScopeByManagedBotId,
      originByManagedBotId,
      scopeByFriendBotId,
      sessionFilterRevisionByBotId,
      sessionKeyByBotId,
    ],
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
    const scope = isManagedConversationSection(section) ? store.effectiveScopeByManagedBotId[botId] ?? 'all' : 'all';
    // 行来源于当前列表缓存;写选中前保留会话快照,过滤重建列表后主会话仍可解析。
    const managedCache = isManagedConversationSection(section) ? store.sessionsByBotId[botId] : undefined;
    const session = isManagedConversationSection(section)
      ? managedCache?.origin === 'mine' && managedCache?.scope === scope
        ? managedCache.sessions.items.find((item) => item.sessionId === sessionId)
        : undefined
      : store.friendBotSessionsByBotId[botId]?.items.find((item) => item.sessionId === sessionId);
    if (session) {
      store.setSelectedSessionSnapshot({
        section,
        botId,
        origin: 'mine',
        scope,
        friendUserId: null,
        session,
        friend: null,
      });
    }
    store.selectConversation({
      botId,
      section,
      origin: 'mine',
      scope,
      friendUserId: null,
      sessionId,
    });
  }, []);
  const loadMoreSessions = useCallback(
    async (botId: string, section: ConversationBotSection, scope: ConversationSessionScope): Promise<void> => {
      const view = botByBotId.get(botId);
      if (!view || view.section !== section) return;
      // friend 范围由 Store 快照在 loadPage 内读取,入参 scope 仅用于 managed 缓存一致性校验。
      if (isManagedConversationSection(section)) {
        const cached = useConversationStore.getState().sessionsByBotId[botId];
        if (!cached || cached.origin !== 'mine' || cached.scope !== scope) return;
      }
      loadPage(view, true);
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
