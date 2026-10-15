// 管理 Bot「查看他人对话」(origin=others)Hook:好友用户目录 + 每好友只读会话分页。
// 懒加载:仅对「已展开且生效 origin=others」的管理 Bot 拉取目录;好友会话仅在该好友
// 分组展开时拉取;未加载的好友分组保留(unloaded),失败分组等待手动重试(无反应式循环)。
// 请求生命周期(并发保护/目录与好友会话请求)拆至 ./managedBotOthersRequests(文件大小
// 阈值):epoch + 代次 + 请求身份元组(含 sessionKey/过滤代次)隔离,用户切换整批作废
// (目录请求 abort);写回前校验 epoch/generation/代次/当前 origin,陈旧响应互不覆盖。
import type { ConversationBotView } from '@/domain/conversation/types';
import { BOT_SESSION_PAGE_SIZE } from '@/services/workspace/botSessionService';
import { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { filterRevisionOf, sessionKeyOf, writeOthersGroup } from './conversationSessionCache';
import {
  abortOthersRequests,
  canWriteOthers,
  createOthersRequestContext,
  loadOthersDirectory,
  loadOthersFriendSessions,
  othersBeginFlight,
  othersEndFlight,
  othersNextGeneration,
  type OthersRequestContext,
} from './managedBotOthersRequests';

export interface ManagedBotOthersModel {
  toggleFriend(botId: string, friendUserId: string): void;
  retryDirectory(botId: string): void;
  retryFriendSessions(botId: string, friendUserId: string): void;
  loadMoreFriendSessions(botId: string, friendUserId: string): Promise<void>;
}

export function useManagedBotOthers(options: {
  userId: string | null;
  managedBots: ConversationBotView[];
  enabled: boolean;
}): ManagedBotOthersModel {
  const { userId, managedBots, enabled } = options;
  const expandedBotIds = useConversationStore((s) => s.expandedBotIds);
  const originByManagedBotId = useConversationStore((s) => s.originByManagedBotId);
  const expandedFriendUserIdsByBotId = useConversationStore((s) => s.expandedFriendUserIdsByBotId);
  const sessionFilterRevisionByBotId = useConversationStore((s) => s.sessionFilterRevisionByBotId);

  const botByBotId = useMemo(() => {
    const map = new Map<string, ConversationBotView>();
    for (const view of managedBots) map.set(view.bot.botId, view);
    return map;
  }, [managedBots]);

  // 请求上下文(epoch/代次/在途 token/abort 控制器)挂载一份;
  // 目录与好友会话的请求生命周期拆见 ./managedBotOthersRequests。
  const requestCtxRef = useRef<OthersRequestContext | null>(null);
  if (!requestCtxRef.current) requestCtxRef.current = createOthersRequestContext(userId);
  const requestCtx = requestCtxRef.current;
  requestCtx.userId.current = userId;
  useEffect(() => {
    requestCtx.epoch.current += 1; // 用户切换:作废全部在途写入并 abort 目录请求;卸载同样清理。
    abortOthersRequests(requestCtx);
    return () => abortOthersRequests(requestCtx);
  }, [requestCtx, userId]);

  const loadDirectory = useRef(
    (view: ConversationBotView, force: boolean): void => loadOthersDirectory(requestCtx, view, force),
  ).current;

  /** 好友会话首拉;force=true 供 retry 用(作废同 key 旧请求,直接重发)。 */
  const loadFriendSessions = useRef(
    (view: ConversationBotView, friendUserId: string, force: boolean): void =>
      loadOthersFriendSessions(requestCtx, view, friendUserId, force),
  ).current;

  // 过滤代次签名:sessionKey/范围等任一实际变化都会刷新;目录与已展开分组按新条件重拉。
  const filterRevisionSignature = useMemo(
    () =>
      Object.keys(sessionFilterRevisionByBotId)
        .sort()
        .map((botId) => `${botId}:${sessionFilterRevisionByBotId[botId] ?? 0}`)
        .join('|'),
    [sessionFilterRevisionByBotId],
  );

  // 目录:仅对已展开且 origin=others 的管理 Bot 自动补拉(悬空 loading 自愈)。
  // 好友会话:仅在该好友分组展开时拉取。
  useEffect(() => {
    if (!enabled || !userId) return;
    for (const botId of Object.keys(expandedBotIds)) {
      const view = botByBotId.get(botId);
      if (!view || (originByManagedBotId[botId] ?? 'mine') !== 'others') continue;
      loadDirectory(view, false);
      for (const friendUserId of Object.keys(expandedFriendUserIdsByBotId[botId] ?? {})) {
        loadFriendSessions(view, friendUserId, false);
      }
    }
  }, [
    botByBotId,
    enabled,
    expandedBotIds,
    expandedFriendUserIdsByBotId,
    filterRevisionSignature,
    loadDirectory,
    loadFriendSessions,
    originByManagedBotId,
    userId,
  ]);

  const othersRequired = useCallback(
    (botId: string): ConversationBotView | null => {
      if (!enabled || !requestCtx.userId.current) return null;
      if ((useConversationStore.getState().originByManagedBotId[botId] ?? 'mine') !== 'others') return null;
      return botByBotId.get(botId) ?? null;
    },
    [botByBotId, enabled, requestCtx],
  );

  const toggleFriend = useCallback((botId: string, friendUserId: string): void => {
    const store = useConversationStore.getState();
    const expanded = Boolean(store.expandedFriendUserIdsByBotId[botId]?.[friendUserId]);
    store.setExpandedFriend(botId, friendUserId, !expanded);
  }, []);

  const retryDirectory = useCallback(
    (botId: string): void => {
      const view = othersRequired(botId);
      if (view) loadDirectory(view, true);
    },
    [loadDirectory, othersRequired],
  );

  const retryFriendSessions = useCallback(
    (botId: string, friendUserId: string): void => {
      const view = othersRequired(botId);
      if (view) loadFriendSessions(view, friendUserId, true);
    },
    [loadFriendSessions, othersRequired],
  );

  const loadMoreFriendSessions = useCallback(
    async (botId: string, friendUserId: string): Promise<void> => {
      const view = othersRequired(botId);
      if (!view) return;
      const store = useConversationStore.getState();
      const group = store.sessionsByBotId[botId]?.friendGroups[friendUserId];
      const loadable = Boolean(group && group.state === 'loaded' && group.sessions.hasMore);
      if (!loadable || !group || group.sessions.loading || group.sessions.isLoadingMore) return;
      const sessionKey = sessionKeyOf(botId, store);
      const revision = filterRevisionOf(botId, store);
      const requestKey = JSON.stringify(['others-friend-sessions', botId, friendUserId, sessionKey, revision, 'more']);
      if (requestCtx.inFlights.has(requestKey)) return;
      const genKey = `fs|${botId}|${friendUserId}`;
      const generation = othersNextGeneration(requestCtx, genKey);
      const epoch = requestCtx.epoch.current;
      const inFlight = othersBeginFlight(requestCtx, requestKey);
      const base = group.sessions;
      writeOthersGroup(botId, friendUserId, (groupNow) => ({
        ...groupNow,
        sessions: { ...base, isLoadingMore: true, loadMoreError: null },
      }));
      const result = await managedBotConversationService.listOtherSessions(
        view.bot,
        friendUserId,
        base.page + 1,
        BOT_SESSION_PAGE_SIZE,
        sessionKey,
      );
      othersEndFlight(requestCtx, requestKey, inFlight);
      if (!canWriteOthers(requestCtx, botId, genKey, generation, epoch, revision)) return;
      writeOthersGroup(botId, friendUserId, (groupNow) => ({
        ...groupNow,
        state: 'loaded' as const,
        sessions: result.ok
          ? { ...result.data, items: [...base.items, ...result.data.items] }
          : { ...base, isLoadingMore: false, loadMoreError: result.error.friendlyMessage },
      }));
    },
    [requestCtx, othersRequired],
  );

  return { toggleFriend, retryDirectory, retryFriendSessions, loadMoreFriendSessions };
}
