// Conversation Store:只提供同步 setter / 选择 / 展开 / 缓存写入 / reset。
// 不调用 API、Service、Toast、Router;网络请求与分页写入由 Hook 经 Service 完成。
import { create } from 'zustand';
import {
  conversationInitialState,
  type ConversationState,
} from './conversationStoreState';
import {
  instanceSelectionSet,
  invalidateFriendBotList,
  invalidateListCaches,
  revisionPatch,
  sessionKeyPatch,
  withoutKey,
} from './conversationStoreHelpers';
import { managedBotOriginPatch, managedBotScopePatch } from './conversationStoreScopePatches';
import { selectedSnapshotMatches, type SelectedSessionSnapshot } from './conversationStoreSnapshot';

export * from './conversationStoreState';

export const useConversationStore = create<ConversationState>((set) => ({
  ...conversationInitialState,
  setExpandedBot: (botId, expanded) =>
    set((state) => {
      if (!expanded) {
        return state.expandedBotIds[botId] ? { expandedBotIds: withoutKey(state.expandedBotIds, botId) } : state;
      }
      if (state.expandedBotIds[botId] && Object.keys(state.expandedBotIds).length === 1) return state;
      // 管理/团队/好友 Bot 共用展开位;仅收起旧列表,缓存、筛选和主会话选中保持不变。
      return { expandedBotIds: { [botId]: true } };
    }),
  setManagedBotOrigin: (botId, origin, options) =>
    set((state) => managedBotOriginPatch(state, botId, origin, options)),
  setManagedBotScope: (botId, scope, options) =>
    set((state) => managedBotScopePatch(state, botId, scope, options)),
  setSessionKeyFilter: (botId, sessionKey) =>
    set((state) => {
      // 提交时只 trim 首尾空白;等值提交(含清空未设置的条件)为 no-op。
      const nextKey = sessionKey.trim();
      if ((state.sessionKeyByBotId[botId] ?? '') === nextKey) return state;
      return {
        ...sessionKeyPatch(state, botId, nextKey),
        // 搜索条件变化使该 Bot 的列表缓存失效(含 others 分组数据,由 Hook 按新条件重拉)。
        ...invalidateListCaches(state, botId),
        ...revisionPatch(state, botId),
      };
    }),
  setBotInstanceSelection: (botId, instanceUuid) =>
    set((state) => instanceSelectionSet(state, botId, instanceUuid.trim())),
  setFriendBotScope: (botId, scope) =>
    set((state) => {
      // 缺省 all:等值提交(no-op)不重放缓存失效与递增。
      if ((state.scopeByFriendBotId[botId] ?? 'all') === scope) return state;
      return {
        scopeByFriendBotId: { ...state.scopeByFriendBotId, [botId]: scope },
        // favorite 走既有收藏接口:立即清空已生效搜索且切回 all 不恢复;列表按新范围重拉。
        ...(scope === 'favorite' ? { sessionKeyByBotId: withoutKey(state.sessionKeyByBotId, botId) } : {}),
        ...invalidateFriendBotList(state, botId),
        ...revisionPatch(state, botId),
      };
    }),
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
    set((state) => ({
      selectedBotId: input.botId,
      selectedSection: input.section,
      selectedOrigin: input.origin,
      selectedScope: input.scope,
      selectedFriendUserId: input.friendUserId,
      selectedSessionId: input.sessionId,
      // 保留快照的中央失效规则:同一选中身份(含 URL 回声重复提交)保留,
      // 任何身份变化(换 Bot/归属/范围/会话/清空)即卸下,不复活旧会话。
      selectedSessionSnapshot: selectedSnapshotMatches(state.selectedSessionSnapshot, input)
        ? state.selectedSessionSnapshot
        : null,
    })),
  setManagedBotCache: (botId, value) =>
    set((state) => ({ sessionsByBotId: { ...state.sessionsByBotId, [botId]: value } })),
  setFriendBotSessions: (botId, value) =>
    set((state) => ({ friendBotSessionsByBotId: { ...state.friendBotSessionsByBotId, [botId]: value } })),
  /** 写入即认领:由各选择动作在写选中前写入同一身份的会话快照(不做身份校验,
   *  失效统一由 selectConversation 的比对与消费方判据负责)。 */
  setSelectedSessionSnapshot: (value: SelectedSessionSnapshot) => set(() => ({ selectedSessionSnapshot: value })),
  reset: () => set(conversationInitialState),
}));
