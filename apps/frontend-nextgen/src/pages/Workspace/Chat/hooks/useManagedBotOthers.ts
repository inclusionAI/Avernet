// 管理 Bot「查看他人对话」(origin=others)Hook:好友用户目录 + 每好友只读会话分页。
// 懒加载:仅对「已展开且生效 origin=others」的管理 Bot 拉取目录;好友会话仅在该好友
// 分组展开时拉取;未加载的好友分组保留(unloaded),失败分组等待手动重试(无反应式循环)。
// 并发保护:{botId, origin} 一代 generation + 请求身份元组(含 sessionKey/过滤代次)隔离,
// 用户切换整批作废(目录请求 abort);写回前校验 epoch/generation/代次/当前 origin,
// 陈旧响应互不覆盖。请求完成只清理自己的在途标记,旧请求不清洗新请求的身份。
import type { ConversationBotView } from '@/domain/conversation/types';
import { BOT_SESSION_PAGE_SIZE } from '@/services/workspace/botSessionService';
import { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  emptySessions,
  filterRevisionOf,
  groupOutcomeOf,
  patchOthersView,
  sessionKeyOf,
  writeOthersGroup,
} from './conversationSessionCache';

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

  const userIdRef = useRef(userId);
  userIdRef.current = userId;
  const epochRef = useRef(0);
  const generationRef = useRef(new Map<string, number>()); // 一代/每保护键。
  /** 在途标记按 token 认领:force 重试覆盖同 key 旧标记时,旧请求完成后不得清洗新请求。 */
  const inFlightRef = useRef(new Map<string, symbol>());
  const controllersRef = useRef(new Map<string, AbortController>());
  const abortAll = useRef((): void => {
    for (const controller of controllersRef.current.values()) controller.abort();
    controllersRef.current.clear();
  }).current;

  const beginFlight = (key: string): symbol => {
    const token = Symbol(key);
    inFlightRef.current.set(key, token);
    return token;
  };
  const endFlight = (key: string, token: symbol): void => {
    if (inFlightRef.current.get(key) === token) inFlightRef.current.delete(key);
  };

  const nextGeneration = useRef((key: string): number => {
    const next = (generationRef.current.get(key) ?? 0) + 1;
    generationRef.current.set(key, next);
    return next;
  }).current;
  /** 写回前的共同校验:epoch、代次、过滤代次、当前 origin 是否仍是 others。 */
  const canWrite = (botId: string, genKey: string, generation: number, epoch: number, revision: number): boolean =>
    epoch === epochRef.current &&
    generationRef.current.get(genKey) === generation &&
    filterRevisionOf(botId, useConversationStore.getState()) === revision &&
    (useConversationStore.getState().originByManagedBotId[botId] ?? 'mine') === 'others';
  useEffect(() => {
    epochRef.current += 1; // 用户切换:作废全部在途写入并 abort 目录请求;卸载同样清理。
    abortAll();
    return () => abortAll();
  }, [abortAll, userId]);

  const loadDirectory = useRef((view: ConversationBotView, force: boolean): void => {
    const botId = view.bot.botId;
    if (!userIdRef.current) return;
    const store = useConversationStore.getState();
    // 目录数据不随 key 变化,但身份须含过滤代次:key 变化删除缓存重拉目录,旧请求不得阻塞/写回。
    const revision = filterRevisionOf(botId, store);
    const requestKey = JSON.stringify(['others-directory', botId, revision]);
    if (inFlightRef.current.has(requestKey)) return;
    const cached = store.sessionsByBotId[botId];
    // 已成功(含确认空列表)不自动拉;失败等待 retryDirectory,避免反应式 effect 循环。
    const loadedOnce = cached?.origin === 'others' && !cached.friendDirectory.loading && !cached.friendDirectory.error;
    if (!force && loadedOnce) return;
    const genKey = `${botId}|others`;
    const generation = nextGeneration(genKey);
    const epoch = epochRef.current;
    const inFlight = beginFlight(requestKey);
    patchOthersView(botId, (item) => ({
      ...item,
      origin: 'others',
      scope: 'all',
      friendDirectory: { ...item.friendDirectory, loading: true, error: null },
    }));
    const controller = new AbortController();
    controllersRef.current.set(requestKey, controller);
    void managedBotConversationService.loadFriendUsers(view.bot, controller.signal).then((result) => {
      controllersRef.current.delete(requestKey);
      endFlight(requestKey, inFlight);
      if (!canWrite(botId, genKey, generation, epoch, revision)) return;
      // 已存在的分组保留会话/加载/展开状态并补写权威好友资料,只对新增好友补充空分组。
      patchOthersView(botId, (item) => {
        if (!result.ok) {
          return {
            ...item,
            friendDirectory: { ...item.friendDirectory, loading: false, error: result.error.friendlyMessage },
          };
        }
        const friendGroups = { ...item.friendGroups };
        for (const user of result.data) {
          // 目录是好友资料的权威来源:分组先于目录建好(过滤变化触发的同轮重拉会以
          // userId 兜底)时,在此补写真实资料;分组的会话/加载/展开状态原样保留。
          const existing = friendGroups[user.userId];
          friendGroups[user.userId] = existing
            ? { ...existing, friend: user }
            : {
                friend: user,
                state: 'unloaded',
                sessions: emptySessions(),
                expanded: false,
              };
        }
        return { ...item, friendDirectory: { items: result.data, loading: false, error: null }, friendGroups };
      });
    });
  }).current;

  /** 好友会话首拉;force=true 供 retry 用(作废同 key 旧请求,直接重发)。 */
  const loadFriendSessions = useRef((view: ConversationBotView, friendUserId: string, force: boolean): void => {
    const botId = view.bot.botId;
    if (!userIdRef.current) return;
    const store = useConversationStore.getState();
    const group = store.sessionsByBotId[botId]?.friendGroups[friendUserId];
    const sessionKey = sessionKeyOf(botId, store);
    const revision = filterRevisionOf(botId, store);
    const requestKey = JSON.stringify(['others-friend-sessions', botId, friendUserId, sessionKey, revision]);
    if (!force) {
      if (inFlightRef.current.has(requestKey)) return;
      // 失败分组等待 retryFriendSessions,避免反应式 effect 循环重试。
      if (group && (group.state === 'loaded' || group.state === 'error')) return;
    }
    const genKey = `fs|${botId}|${friendUserId}`;
    const generation = nextGeneration(genKey);
    const epoch = epochRef.current;
    const inFlight = beginFlight(requestKey);
    patchOthersView(botId, (item) => ({
      ...item,
      origin: 'others',
      scope: 'all',
      friendGroups: {
        ...item.friendGroups,
        [friendUserId]: {
          friend: group?.friend ??
            item.friendDirectory.items.find((user) => user.userId === friendUserId) ?? {
              userId: friendUserId,
              displayName: friendUserId,
            },
          state: 'loading',
          expanded: useConversationStore.getState().expandedFriendUserIdsByBotId[botId]?.[friendUserId] === true,
          sessions: {
            ...(group?.sessions ?? emptySessions()),
            loading: true,
            error: null,
          },
        },
      },
    }));
    void managedBotConversationService
      .listOtherSessions(view.bot, friendUserId, 1, BOT_SESSION_PAGE_SIZE, sessionKey)
      .then((result) => {
        endFlight(requestKey, inFlight);
        if (!canWrite(botId, genKey, generation, epoch, revision)) return;
        const expandedStill =
          useConversationStore.getState().expandedFriendUserIdsByBotId[botId]?.[friendUserId] === true;
        writeOthersGroup(botId, friendUserId, (groupNow) => ({
          ...groupNow,
          expanded: expandedStill,
          ...groupOutcomeOf(result, groupNow.sessions),
        }));
      });
  }).current;

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
      if (!enabled || !userIdRef.current) return null;
      if ((useConversationStore.getState().originByManagedBotId[botId] ?? 'mine') !== 'others') return null;
      return botByBotId.get(botId) ?? null;
    },
    [botByBotId, enabled],
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
      if (inFlightRef.current.has(requestKey)) return;
      const genKey = `fs|${botId}|${friendUserId}`;
      const generation = nextGeneration(genKey);
      const epoch = epochRef.current;
      const inFlight = beginFlight(requestKey);
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
      endFlight(requestKey, inFlight);
      if (!canWrite(botId, genKey, generation, epoch, revision)) return;
      writeOthersGroup(botId, friendUserId, (groupNow) => ({
        ...groupNow,
        state: 'loaded' as const,
        sessions: result.ok
          ? { ...result.data, items: [...base.items, ...result.data.items] }
          : { ...base, isLoadingMore: false, loadMoreError: result.error.friendlyMessage },
      }));
    },
    [canWrite, nextGeneration, othersRequired],
  );

  return { toggleFriend, retryDirectory, retryFriendSessions, loadMoreFriendSessions };
}
