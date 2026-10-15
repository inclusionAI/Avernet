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

export function useConversationSessionResolution(options: {
  store: ConversationState;
  botById: Map<string, ChatBotView>;
  /** 选中 Bot 是否仍在目录(目录外的 mine 会话按无效处理,不渲染交互舞台)。 */
  selectedInDirectory: boolean;
  origin: 'mine' | 'others';
}) {
  const { store, botById, selectedInDirectory, origin } = options;

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

  return { interactive, readonly };
}