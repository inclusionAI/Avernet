// 会话页「选中 ↔ URL」装配 Hook(Task 8 页边界专用):
// - 出向:Store 选中态 → ConversationRouteState(经 useConversationUrlSync 投影);
// - 入向:URL → Store 装配在 useConversationRouteResolver(展开 Bot/好友分组、
//   记忆归属/范围、单选会话、hydration 重解析);
// - 推导:当前选中对应的交互式(mine)与只读(others)视图数据在
//   useConversationSessionResolution(列表解析 + 快照兜底 + 中央认领)。
// 两个子 Hook 均为本文件大小阈值拆分,装配语义与调用方契约不变。
import type { ConversationBotView, ConversationRouteState, ConversationUserView } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { useConversationStore, type ConversationState } from '@/stores/conversationStore';
import { useCallback, useMemo } from 'react';
import { useConversationRouteResolver } from './useConversationRouteResolver';
import { useConversationSessionResolution } from './useConversationSessionResolution';

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

  const { interactive, readonly } = useConversationSessionResolution({
    store,
    botById,
    selectedInDirectory,
    origin,
  });
  const onRouteSelection = useConversationRouteResolver({
    managedBots,
    friendBots,
    hydrated,
    managedBotIdKey,
    friendBotIdKey,
    selectedInDirectory,
    selection,
    store,
  });

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