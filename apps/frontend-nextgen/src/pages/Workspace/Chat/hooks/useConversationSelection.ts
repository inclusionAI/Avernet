// 会话页「选中 ↔ URL」装配 Hook(Task 8 页边界专用):
// - 出向:Store 选中态 → ConversationRouteState(经 useConversationUrlSync 投影);
// - 入向:URL → Store(展开 Bot/好友分组、记忆归属/范围、单选会话);
// - 推导:当前选中对应的交互式(mine)与只读(others)视图数据,交由页面分支渲染。
// section 未解析的旧/外部链接原样透传;目录 hydration 完成后按目录重解析一次。
import type { ConversationBotView, ConversationRouteState, ConversationUserView } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { useConversationStore, type ConversationState } from '@/stores/conversationStore';
import { useCallback, useEffect, useMemo, useRef } from 'react';

export interface UseConversationSelectionResult {
  /** 会话 Store 快照(侧栏等消费方用;页随 Store 变化重渲染)。 */
  store: ConversationState;
  /** URL 投影:当前选中状态的会话路由契约。 */
  selection: ConversationRouteState;
  /** 当前选中的归属(管理 Bot 按选择记录,好友 Bot 恒 mine)。 */
  origin: 'mine' | 'others';
  /** 交互式分支数据:选中 Bot 及其 mine 会话(任一缺失即无交互目标)。 */
  interactive: { bot: ChatBotView | null; session: BotChatSessionView | null };
  /** 只读分支数据(origin=others):Bot/好友/只读会话,任一缺失面板降级空态。 */
  readonly: {
    bot: ChatBotView | null;
    friend: ConversationUserView | null;
    session: BotChatSessionView | null;
  };
  /** 入向:URL 解析结果写回 Store(useConversationUrlSync 回调)。 */
  onRouteSelection(route: ConversationRouteState): void;
  /** 只读会话选中(侧栏 origin=others 行,含 bot/好友/会话三元组)。 */
  selectReadonlySession(botId: string, sessionId: string, friendUserId: string): void;
  /** 清空选中(对照 mine/others 分支的空态)。 */
  clearSelection(): void;
}

export function useConversationSelection(options: {
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  /** 目录 hydration 是否完成(完成后才按目录重解析未决 section)。 */
  hydrated: boolean;
}): UseConversationSelectionResult {
  const { managedBots, friendBots, hydrated } = options;
  const store = useConversationStore();

  const botById = useMemo(() => {
    const map = new Map<string, ChatBotView>();
    for (const view of [...managedBots, ...friendBots]) map.set(view.bot.botId, view.bot);
    return map;
  }, [managedBots, friendBots]);
  // 目录 key 变化才触发重解析 effect(数组引用每次渲染都变)。
  const managedBotIdKey = useMemo(() => managedBots.map((v) => `${v.section}=${v.bot.botId}`).join('|'), [managedBots]);
  const friendBotIdKey = useMemo(() => friendBots.map((v) => v.bot.botId).join('|'), [friendBots]);

  const selectedInDirectory =
    store.selectedSection === 'friend'
      ? friendBotIdKey.split('|').includes(store.selectedBotId ?? '')
      : managedBotIdKey.split('|').includes(`${store.selectedSection}=${store.selectedBotId}`);
  const origin: 'mine' | 'others' =
    store.selectedBotId !== null && isManagedConversationSection(store.selectedSection) ? store.selectedOrigin : 'mine';
  const selection = useMemo<ConversationRouteState>(() => {
    const managedSelection = isManagedConversationSection(store.selectedSection);
    return {
      botId: store.selectedBotId ?? undefined,
      section: store.selectedSection ?? undefined,
      origin: managedSelection ? store.selectedOrigin : undefined,
      scope: managedSelection && store.selectedOrigin === 'mine' ? store.selectedScope : undefined,
      friendUserId:
        managedSelection && store.selectedOrigin === 'others' ? store.selectedFriendUserId ?? undefined : undefined,
      sessionId: store.selectedSessionId ?? undefined,
    };
  }, [
    store.selectedBotId,
    store.selectedSection,
    store.selectedOrigin,
    store.selectedScope,
    store.selectedFriendUserId,
    store.selectedSessionId,
  ]);

  const mineSession = useMemo<BotChatSessionView | null>(() => {
    const botId = store.selectedBotId;
    const sessionId = store.selectedSessionId;
    if (!botId || !sessionId || store.selectedOrigin !== 'mine') return null;
    if (isManagedConversationSection(store.selectedSection)) {
      const cache = store.sessionsByBotId[botId];
      return cache && cache.origin === 'mine' && cache.scope === store.selectedScope
        ? cache.sessions.items.find((item) => item.sessionId === sessionId) ?? null
        : null;
    }
    if (store.selectedSection === 'friend') {
      return store.friendBotSessionsByBotId[botId]?.items.find((item) => item.sessionId === sessionId) ?? null;
    }
    return null;
  }, [
    store.selectedBotId,
    store.selectedSection,
    store.selectedSessionId,
    store.selectedOrigin,
    store.selectedScope,
    store.sessionsByBotId,
    store.friendBotSessionsByBotId,
  ]);

  const interactive = useMemo(() => {
    const bot = mineSession && selectedInDirectory ? botById.get(mineSession.botId) ?? null : null;
    return { bot, session: bot ? mineSession : null };
  }, [botById, mineSession, selectedInDirectory]);

  const readonly = useMemo(() => {
    const botId = store.selectedBotId;
    const friendUserId = store.selectedFriendUserId;
    if (!botId || origin !== 'others') {
      return { bot: null, friend: null, session: null };
    }
    const group = store.sessionsByBotId[botId]?.friendGroups[friendUserId ?? ''];
    const session = group?.sessions.items.find((item) => item.sessionId === store.selectedSessionId) ?? null;
    return {
      bot: botById.get(botId) ?? null,
      friend: group?.friend ?? null,
      session,
    };
  }, [
    botById,
    origin,
    store.selectedBotId,
    store.selectedFriendUserId,
    store.selectedSessionId,
    store.sessionsByBotId,
  ]);

  // 入向:URL → Store。section 缺失且目录可匹配时补齐;目录无匹配保持 null,
  // 待 hydration 完成后由下方 effect 重解析(旧/外部链接原样透传)。
  const lastRouteRef = useRef<ConversationRouteState | null>(null);
  const onRouteSelection = useCallback(
    (route: ConversationRouteState) => {
      // 完全空的解析(无参数,如同路由直进 /workspace/chat)不动选中:
      // 挂载清空会误清除页内已有选中;URL 投影对空选中本就产出空串。
      if (!route.botId && !route.section && !route.sessionId) return;
      lastRouteRef.current = route;
      const storeNow = useConversationStore.getState();
      if (!route.botId) {
        storeNow.selectConversation({
          botId: null,
          section: null,
          origin: 'mine',
          scope: 'all',
          friendUserId: null,
          sessionId: null,
        });
        return;
      }
      const section =
        route.section ??
        (managedBotIdKey.split('|').includes(`managed=${route.botId}`)
          ? 'managed'
          : managedBotIdKey.split('|').includes(`team=${route.botId}`)
          ? 'team'
          : friendBotIdKey.split('|').includes(route.botId)
          ? 'friend'
          : null);
      const managedOrigin = isManagedConversationSection(section) ? route.origin ?? 'mine' : 'mine';
      const scope =
        isManagedConversationSection(section) && managedOrigin === 'mine'
          ? route.scope ?? storeNow.effectiveScopeByManagedBotId[route.botId] ?? 'all'
          : 'all';
      const friendUserId =
        isManagedConversationSection(section) && managedOrigin === 'others' ? route.friendUserId ?? null : null;
      // origin=others 缺 friend 的非法 URL:sessionId 无法归属到某好友,从选中态剔除,
      // 避免投影回写 origin=others&session=… 残缺组合(AC-13 路由语法)。
      const sessionId =
        isManagedConversationSection(section) && managedOrigin === 'others' && !friendUserId
          ? null
          : route.sessionId ?? null;
      storeNow.setExpandedBot(route.botId, true);
      if (isManagedConversationSection(section)) {
        if ((storeNow.originByManagedBotId[route.botId] ?? 'mine') !== managedOrigin) {
          storeNow.setManagedBotOrigin(route.botId, managedOrigin);
        }
        if ((storeNow.effectiveScopeByManagedBotId[route.botId] ?? 'all') !== scope) {
          storeNow.setManagedBotScope(route.botId, scope);
        }
        if (friendUserId) storeNow.setExpandedFriend(route.botId, friendUserId, true);
      }
      storeNow.selectConversation({
        botId: route.botId,
        section,
        origin: managedOrigin,
        scope,
        friendUserId,
        sessionId,
      });
    },
    [friendBotIdKey, managedBotIdKey],
  );

  // 目录 hydration 完成后,对仍未解析 section 的待定路由重解析一次。
  useEffect(() => {
    if (!hydrated || !lastRouteRef.current) return;
    const storeNow = useConversationStore.getState();
    if (!storeNow.selectedBotId || storeNow.selectedSection) return;
    onRouteSelection(lastRouteRef.current);
  }, [friendBotIdKey, hydrated, managedBotIdKey, onRouteSelection]);

  // 完整目录到达后按管理→团队→好友归类；不能拿旧 friend 缓存搭配 team 请求身份。
  useEffect(() => {
    if (!hydrated || selectedInDirectory || !store.selectedBotId) return;
    const section =
      managedBots.find((view) => view.bot.botId === store.selectedBotId)?.section ??
      friendBots.find((view) => view.bot.botId === store.selectedBotId)?.section;
    if (section) onRouteSelection({ ...selection, section });
  }, [hydrated, selectedInDirectory, store.selectedBotId, managedBots, friendBots, onRouteSelection, selection]);

  const selectReadonlySession = useCallback(
    (botId: string, sessionId: string, friendUserId: string) => {
      const storeNow = useConversationStore.getState();
      storeNow.setExpandedBot(botId, true);
      storeNow.setExpandedFriend(botId, friendUserId, true);
      storeNow.selectConversation({
        botId,
        section: managedBotIdKey.split('|').includes(`team=${botId}`) ? 'team' : 'managed',
        origin: 'others',
        scope: 'all',
        friendUserId,
        sessionId,
      });
    },
    [managedBotIdKey],
  );

  const clearSelection = useCallback(() => {
    useConversationStore.getState().selectConversation({
      botId: null,
      section: null,
      origin: 'mine',
      scope: 'all',
      friendUserId: null,
      sessionId: null,
    });
  }, []);

  return { store, selection, origin, interactive, readonly, onRouteSelection, selectReadonlySession, clearSelection };
}
