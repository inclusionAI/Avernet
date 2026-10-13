// Conversation Store:只提供同步 setter / 选择 / 展开 / 缓存写入 / reset。
// 不调用 API、Service、Toast、Router;网络请求与分页写入由 Hook 经 Service 完成。
import type { ConversationSessionScope } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { create } from 'zustand';
import { conversationInitialState, type ConversationState } from './conversationStoreState';

export * from './conversationStoreState';

function withoutKey(record: Record<string, true>, key: string): Record<string, true> {
  const next = { ...record };
  delete next[key];
  return next;
}

export const useConversationStore = create<ConversationState>((set) => ({
  ...conversationInitialState,
  setExpandedBot: (botId, expanded) =>
    set((state) => {
      if (!expanded)
        return state.expandedBotIds[botId] ? { expandedBotIds: withoutKey(state.expandedBotIds, botId) } : state;
      if (state.expandedBotIds[botId] && Object.keys(state.expandedBotIds).length === 1) return state;
      // 管理/团队/好友 Bot 共用展开位；仅收起旧列表，缓存、筛选和主会话选中保持不变。
      return { expandedBotIds: { [botId]: true } };
    }),
  setManagedBotOrigin: (botId, origin) =>
    set((state) => {
      if (state.originByManagedBotId[botId] === origin) return state;
      // effective 读取范围:others 强制 all;mine 恢复记忆范围(缺省 all)。
      const effectiveScope: ConversationSessionScope =
        origin === 'others' ? 'all' : state.scopeByManagedBotId[botId] ?? 'all';
      const selectedHere = state.selectedBotId === botId && isManagedConversationSection(state.selectedSection);
      return {
        originByManagedBotId: { ...state.originByManagedBotId, [botId]: origin },
        effectiveScopeByManagedBotId: { ...state.effectiveScopeByManagedBotId, [botId]: effectiveScope },
        // 发起归属切换后该 Bot 的 Session 列表整体失效,清除其选中的 Session;
        // 所选 Bot 的选中态同步跟随筛选(AC-6/AC-7:主舞台与 URL 投影随切换一致)。
        ...(selectedHere
          ? {
              selectedOrigin: origin,
              selectedScope: effectiveScope,
              // 来源切换后好友只读选中不再成立,一并无效化(与 selectedSessionId 同语义)。
              selectedFriendUserId: null,
              selectedSessionId: null,
            }
          : {}),
      };
    }),
  setManagedBotScope: (botId, scope) =>
    set((state) => ({
      scopeByManagedBotId: { ...state.scopeByManagedBotId, [botId]: scope },
      effectiveScopeByManagedBotId: { ...state.effectiveScopeByManagedBotId, [botId]: scope },
      // 所选管理 Bot 且当前归属为 mine 时,选中范围同步跟随(AC-7 筛选投影 URL)。
      ...(state.selectedBotId === botId &&
      isManagedConversationSection(state.selectedSection) &&
      state.selectedOrigin === 'mine'
        ? { selectedScope: scope }
        : {}),
    })),
  setExpandedFriend: (botId, friendUserId, expanded) =>
    set((state) => {
      const current = state.expandedFriendUserIdsByBotId[botId] ?? {};
      if (current[friendUserId] === expanded) return state;
      return {
        expandedFriendUserIdsByBotId: {
          ...state.expandedFriendUserIdsByBotId,
          [botId]: expanded ? { ...current, [friendUserId]: true } : withoutKey(current, friendUserId),
        },
      };
    }),
  setBotGroupCollapsed: (groupId, collapsed) =>
    set((state) => {
      if ((state.collapsedBotGroups[groupId] === true) === collapsed) return state;
      return {
        collapsedBotGroups: collapsed
          ? { ...state.collapsedBotGroups, [groupId]: true }
          : withoutKey(state.collapsedBotGroups, groupId),
      };
    }),
  selectConversation: (input) =>
    set({
      selectedBotId: input.botId,
      selectedSection: input.section,
      selectedOrigin: input.origin,
      selectedScope: input.scope,
      selectedFriendUserId: input.friendUserId,
      selectedSessionId: input.sessionId,
    }),
  setManagedBotCache: (botId, value) =>
    set((state) => ({ sessionsByBotId: { ...state.sessionsByBotId, [botId]: value } })),
  setFriendBotSessions: (botId, value) =>
    set((state) => ({ friendBotSessionsByBotId: { ...state.friendBotSessionsByBotId, [botId]: value } })),
  reset: () => set(conversationInitialState),
}));
