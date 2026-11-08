// 当前选中会话的解析子 Hook(从 useConversationSelection 拆出,文件大小阈值):
// - 生活跃解析:只查当前列表缓存,不含保留快照(认领/回退判据不得互相引用,避免渲染期写回循环);
// - 快照兜底:列表因过滤条件重建(搜索加载/空/失败/未命中)失去该会话时,保留快照仍
//   驱动已打开的主会话;身份与快照不符(已完成切换/清空等)不允许复活旧会话;
// - 中央认领:无显式写点的选中(URL 深链/删除回退)在解析出生活的当前选中会话后
//   由这里补写/刷新快照(详见各块内注释)。
import type { ConversationUserView } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { useConversationStore, type ConversationState } from '@/stores/conversationStore';
import { selectedSnapshotMatches } from '@/stores/conversationStoreState';
import { useEffect, useMemo } from 'react';

interface LiveInteractiveSession {
  session: BotChatSessionView;
  friend: ConversationUserView | null;
}

export function useConversationSessionResolution(options: {
  store: ConversationState;
  botById: Map<string, ChatBotView>;
  /** 选中 Bot 是否仍在目录(目录外的会话按无效处理,不渲染交互舞台)。 */
  selectedInDirectory: boolean;
  origin: 'mine' | 'others';
}) {
  const { store, botById, selectedInDirectory, origin } = options;
  const selectedBot = store.selectedBotId ? botById.get(store.selectedBotId) ?? null : null;
  const selectedIsCodingBot = selectedBot?.isAgentCodingBot === true;

  // 普通 Bot 仅 mine 可交互；Coding Bot 的 others 仍由 AIXCoding 子应用承接，因此
  // 同样从当前来源的扁平 sessions 解析为交互会话；旧缓存中若仍保留好友分组，
  // 继续兼容解析，避免升级过程中已经打开的会话被关闭。
  const liveInteractive = useMemo<LiveInteractiveSession | null>(() => {
    const botId = store.selectedBotId;
    const sessionId = store.selectedSessionId;
    if (!botId || !sessionId || (!selectedIsCodingBot && store.selectedOrigin !== 'mine')) return null;
    if (isManagedConversationSection(store.selectedSection)) {
      const cache = store.sessionsByBotId[botId];
      if (cache && cache.origin === store.selectedOrigin && cache.scope === store.selectedScope) {
        const session = cache.sessions.items.find((item) => item.sessionId === sessionId);
        if (session) return { session, friend: null };
      }
      if (selectedIsCodingBot && store.selectedOrigin === 'others') {
        const group = cache?.friendGroups[store.selectedFriendUserId ?? ''];
        const session = group?.sessions.items.find((item) => item.sessionId === sessionId);
        if (session && group) return { session, friend: group.friend };
      }
      return null;
    }
    if (store.selectedSection === 'friend' && store.selectedOrigin === 'mine') {
      const session = store.friendBotSessionsByBotId[botId]?.items.find((item) => item.sessionId === sessionId);
      return session ? { session, friend: null } : null;
    }
    return null;
  }, [
    selectedIsCodingBot,
    store.selectedBotId,
    store.selectedSection,
    store.selectedSessionId,
    store.selectedOrigin,
    store.selectedScope,
    store.selectedFriendUserId,
    store.sessionsByBotId,
    store.friendBotSessionsByBotId,
  ]);

  const interactiveSession = useMemo<BotChatSessionView | null>(() => {
    if (liveInteractive) return liveInteractive.session;
    if (!store.selectedBotId || !store.selectedSessionId) return null;
    if (!selectedIsCodingBot && store.selectedOrigin !== 'mine') return null;
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
    liveInteractive,
    selectedIsCodingBot,
    store.selectedBotId,
    store.selectedSection,
    store.selectedSessionId,
    store.selectedOrigin,
    store.selectedScope,
    store.selectedFriendUserId,
    store.selectedSessionSnapshot,
  ]);

  const interactive = useMemo(() => {
    const bot = interactiveSession && selectedInDirectory ? selectedBot : null;
    return { bot, session: bot ? interactiveSession : null };
  }, [interactiveSession, selectedBot, selectedInDirectory]);

  const liveReadonly = useMemo(() => {
    const botId = store.selectedBotId;
    if (!botId || origin !== 'others' || selectedIsCodingBot) return null;
    const group = store.sessionsByBotId[botId]?.friendGroups[store.selectedFriendUserId ?? ''];
    const session = group?.sessions.items.find((item) => item.sessionId === store.selectedSessionId) ?? null;
    return session && group ? { friend: group.friend, session } : null;
  }, [
    origin,
    selectedIsCodingBot,
    store.selectedBotId,
    store.selectedFriendUserId,
    store.selectedSessionId,
    store.sessionsByBotId,
  ]);

  const readonly = useMemo(() => {
    const botId = store.selectedBotId;
    const friendUserId = store.selectedFriendUserId;
    if (!botId || origin !== 'others' || selectedIsCodingBot) {
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
      bot: selectedBot,
      friend: liveReadonly?.friend ?? (snapshotActive ? snapshot.friend : null),
      session: liveReadonly?.session ?? (snapshotActive ? snapshot.session : null),
    };
  }, [
    liveReadonly,
    origin,
    selectedBot,
    selectedIsCodingBot,
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
      liveInteractive && liveInteractive.session.sessionId === identity.sessionId
        ? liveInteractive
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
  }, [liveInteractive, liveReadonly]);

  return { interactive, readonly };
}
