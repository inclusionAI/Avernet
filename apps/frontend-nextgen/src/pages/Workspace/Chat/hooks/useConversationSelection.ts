// 会话页「选中 ↔ URL」装配 Hook(Task 8 页边界专用):
// - 出向:Store 选中态 → ConversationRouteState(经 useConversationUrlSync 投影);
// - 入向:URL → Store(展开 Bot/好友分组、记忆归属/范围、单选会话);
// - 推导:当前选中对应的交互式(mine)与只读(others)视图数据,交由页面分支渲染。
// section 未解析的旧/外部链接原样透传;目录 hydration 完成后按目录重解析一次。
import type { ConversationBotView, ConversationRouteState, ConversationUserView } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { useConversationStore, type ConversationState } from '@/stores/conversationStore';
import { selectedSnapshotMatches } from '@/stores/conversationStoreState';
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

  // 生活跃解析:只查当前列表缓存,不含保留快照(认领/回退判据不得互相引用,避免渲染期写回循环)。
  const liveMine = useMemo<BotChatSessionView | null>(() => {
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

  const mineSession = useMemo<BotChatSessionView | null>(() => {
    if (liveMine) return liveMine;
    if (!store.selectedBotId || !store.selectedSessionId) return null;
    // 列表因过滤条件重建(搜索加载/空/失败/未命中)而失去该会话时,保留快照仍
    // 驱动已打开的主会话;身份与快照不符(已完成切换/清空等)不允许复活旧会话。
    return selectedSnapshotMatches(store.selectedSessionSnapshot, {
      botId: store.selectedBotId,
      section: store.selectedSection,
      origin: store.selectedOrigin,
      scope: store.selectedScope,
      friendUserId: store.selectedFriendUserId,
      sessionId: store.selectedSessionId,
    })
      ? store.selectedSessionSnapshot.session
      : null;
  }, [
    liveMine,
    store.selectedBotId,
    store.selectedSection,
    store.selectedSessionId,
    store.selectedOrigin,
    store.selectedScope,
    store.selectedFriendUserId,
    store.selectedSessionSnapshot,
  ]);

  const interactive = useMemo(() => {
    const bot = mineSession && selectedInDirectory ? botById.get(mineSession.botId) ?? null : null;
    return { bot, session: bot ? mineSession : null };
  }, [botById, mineSession, selectedInDirectory]);

  const liveReadonly = useMemo(() => {
    const botId = store.selectedBotId;
    if (!botId || origin !== 'others') return null;
    const group = store.sessionsByBotId[botId]?.friendGroups[store.selectedFriendUserId ?? ''];
    const session = group?.sessions.items.find((item) => item.sessionId === store.selectedSessionId) ?? null;
    return session && group ? { friend: group.friend, session } : null;
  }, [origin, store.selectedBotId, store.selectedFriendUserId, store.selectedSessionId, store.sessionsByBotId]);

  const readonly = useMemo(() => {
    const botId = store.selectedBotId;
    const friendUserId = store.selectedFriendUserId;
    if (!botId || origin !== 'others') {
      return { bot: null, friend: null, session: null };
    }
    const snapshot = store.selectedSessionSnapshot;
    const snapshotActive = selectedSnapshotMatches(snapshot, {
      botId,
      section: store.selectedSection,
      origin: store.selectedOrigin,
      scope: store.selectedScope,
      friendUserId,
      sessionId: store.selectedSessionId,
    });
    // 过滤变化删除 others 分组缓存时,快照同时兜底只读舞台的好友资料。
    return {
      bot: botById.get(botId) ?? null,
      friend: liveReadonly?.friend ?? (snapshotActive ? snapshot.friend : null),
      session: liveReadonly?.session ?? (snapshotActive ? snapshot.session : null),
    };
  }, [
    botById,
    liveReadonly,
    origin,
    store.selectedBotId,
    store.selectedSection,
    store.selectedOrigin,
    store.selectedScope,
    store.selectedFriendUserId,
    store.selectedSessionId,
    store.selectedSessionSnapshot,
  ]);

  // 中央认领:无显式写点的选中(URL 深链/删除回退)在解析出生活的当前选中会话后
  // 由这里补写/刷新快照;live 对象被重拉替换时同步更新元数据。
  // 写入前按提交时最新 Store 复核选中身份,不做过期捕获;快照已持有同一对象则跳过,
  // 避免任何渲染期写回循环。
  useEffect(() => {
    const storeNow = useConversationStore.getState();
    const identity = {
      botId: storeNow.selectedBotId,
      section: storeNow.selectedSection,
      origin: storeNow.selectedOrigin,
      scope: storeNow.selectedScope,
      friendUserId: storeNow.selectedFriendUserId,
      sessionId: storeNow.selectedSessionId,
    };
    if (!identity.botId || !identity.section || !identity.sessionId) return;
    const live =
      identity.origin === 'mine'
        ? liveMine && liveMine.sessionId === identity.sessionId
          ? { session: liveMine, friend: null as ConversationUserView | null }
          : null
        : liveReadonly && liveReadonly.session.sessionId === identity.sessionId
        ? liveReadonly
        : null;
    if (!live) return;
    const snapshot = storeNow.selectedSessionSnapshot;
    if (
      selectedSnapshotMatches(snapshot, identity) &&
      snapshot.session === live.session &&
      snapshot.friend === live.friend
    )
      return;
    storeNow.setSelectedSessionSnapshot({
      section: identity.section,
      botId: identity.botId,
      origin: identity.origin,
      scope: identity.scope,
      friendUserId: identity.friendUserId,
      session: live.session,
      friend: live.friend,
    });
    // live 单独来自列表缓存与选中身份,不依赖快照字段,认领写回不会再次触发本 effect。
  }, [liveMine, liveReadonly]);

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
      const section: 'team' | 'managed' = managedBotIdKey.split('|').includes(`team=${botId}`) ? 'team' : 'managed';
      // 行来源于当前分组缓存,写选中前保留会话与好友资料快照(过滤重建缓存后仍可解析)。
      const group = storeNow.sessionsByBotId[botId]?.friendGroups[friendUserId];
      const session = group?.sessions.items.find((item) => item.sessionId === sessionId);
      if (group && session) {
        storeNow.setSelectedSessionSnapshot({
          section,
          botId,
          origin: 'others',
          scope: 'all',
          friendUserId,
          session,
          friend: group.friend,
        });
      }
      storeNow.selectConversation({
        botId,
        section,
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
