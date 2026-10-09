// 管理 Bot「查看他人对话」(origin=others)Hook:好友用户目录 + 每好友只读会话分页。
// 懒加载:仅对「已展开且生效 origin=others」的管理 Bot 拉取目录;好友会话仅在该好友
// 分组展开时拉取;未加载的好友分组保留(unloaded),失败分组等待手动重试(无反应式循环)。
// 并发保护:{botId, origin} 一代 generation + 请求 key 隔离,用户切换整批作废(目录请求
// abort);写回前校验 epoch/generation/当前 origin,陈旧响应互不覆盖。
import type { ConversationBotView } from '@/domain/conversation/types';
import { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { emptySessions, groupOutcomeOf, patchOthersView, writeOthersGroup } from './conversationSessionCache';

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

  const botByBotId = useMemo(() => {
    const map = new Map<string, ConversationBotView>();
    for (const view of managedBots) map.set(view.bot.botId, view);
    return map;
  }, [managedBots]);

  const userIdRef = useRef(userId);
  userIdRef.current = userId;
  const epochRef = useRef(0);
  const generationRef = useRef(new Map<string, number>()); // 一代/每保护键。
  const inFlightRef = useRef(new Set<string>());
  const controllersRef = useRef(new Map<string, AbortController>());
  const abortAll = useRef((): void => {
    for (const controller of controllersRef.current.values()) controller.abort();
    controllersRef.current.clear();
  }).current;

  const nextGeneration = useRef((key: string): number => {
    const next = (generationRef.current.get(key) ?? 0) + 1;
    generationRef.current.set(key, next);
    return next;
  }).current;
  /** 写回前的共同校验:epoch、代次、当前 origin 是否仍是 others。 */
  const canWrite = (botId: string, genKey: string, generation: number, epoch: number): boolean =>
    epoch === epochRef.current &&
    generationRef.current.get(genKey) === generation &&
    (useConversationStore.getState().originByManagedBotId[botId] ?? 'mine') === 'others';
  useEffect(() => {
    epochRef.current += 1; // 用户切换:作废全部在途写入并 abort 目录请求;卸载同样清理。
    abortAll();
    return () => abortAll();
  }, [abortAll, userId]);

  const loadDirectory = useRef((view: ConversationBotView, force: boolean): void => {
    const botId = view.bot.botId;
    const requestKey = `directory:${botId}`;
    if (!userIdRef.current) return;
    if (inFlightRef.current.has(requestKey)) return;
    const cached = useConversationStore.getState().sessionsByBotId[botId];
    // 已成功(含确认空列表)不自动拉;失败等待 retryDirectory,避免反应式 effect 循环。
    const loadedOnce = cached?.origin === 'others' && !cached.friendDirectory.loading && !cached.friendDirectory.error;
    if (!force && loadedOnce) return;
    const genKey = `${botId}|others`;
    const generation = nextGeneration(genKey);
    const epoch = epochRef.current;
    inFlightRef.current.add(requestKey);
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
      inFlightRef.current.delete(requestKey);
      if (!canWrite(botId, genKey, generation, epoch)) return;
      // 已存在的分组(未加载/已加载)一律保留,只补充目录新增的好友。
      patchOthersView(botId, (item) => {
        if (!result.ok) {
          return {
            ...item,
            friendDirectory: { ...item.friendDirectory, loading: false, error: result.error.friendlyMessage },
          };
        }
        const friendGroups = { ...item.friendGroups };
        for (const user of result.data) {
          friendGroups[user.userId] ??= {
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
    const requestKey = `sessions:${botId}:${friendUserId}`;
    if (!userIdRef.current) return;
    const group = useConversationStore.getState().sessionsByBotId[botId]?.friendGroups[friendUserId];
    if (!force) {
      if (inFlightRef.current.has(requestKey)) return;
      // 失败分组等待 retryFriendSessions,避免反应式 effect 循环重试。
      if (group && (group.state === 'loaded' || group.state === 'error')) return;
    }
    const genKey = `fs|${botId}|${friendUserId}`;
    const generation = nextGeneration(genKey);
    const epoch = epochRef.current;
    inFlightRef.current.add(requestKey);
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
    void managedBotConversationService.listOtherSessions(view.bot, friendUserId).then((result) => {
      inFlightRef.current.delete(requestKey);
      if (!canWrite(botId, genKey, generation, epoch)) return;
      const expandedStill =
        useConversationStore.getState().expandedFriendUserIdsByBotId[botId]?.[friendUserId] === true;
      writeOthersGroup(botId, friendUserId, (groupNow) => ({
        ...groupNow,
        expanded: expandedStill,
        ...groupOutcomeOf(result, groupNow.sessions),
      }));
    });
  }).current;

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
      const requestKey = `sessions:${botId}:${friendUserId}`;
      const group = useConversationStore.getState().sessionsByBotId[botId]?.friendGroups[friendUserId];
      const loadable = Boolean(group && group.state === 'loaded' && group.sessions.hasMore);
      if (!loadable || !group || group.sessions.loading || group.sessions.isLoadingMore) return;
      if (inFlightRef.current.has(requestKey)) return;
      const genKey = `fs|${botId}|${friendUserId}`;
      const generation = nextGeneration(genKey);
      const epoch = epochRef.current;
      const base = group.sessions;
      inFlightRef.current.add(requestKey);
      writeOthersGroup(botId, friendUserId, (groupNow) => ({
        ...groupNow,
        sessions: { ...base, isLoadingMore: true, loadMoreError: null },
      }));
      const result = await managedBotConversationService.listOtherSessions(view.bot, friendUserId, base.page + 1);
      inFlightRef.current.delete(requestKey);
      if (!canWrite(botId, genKey, generation, epoch)) return;
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
