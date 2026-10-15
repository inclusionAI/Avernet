// Conversation Store:只提供同步 setter / 选择 / 展开 / 缓存写入 / reset。
// 不调用 API、Service、Toast、Router;网络请求与分页写入由 Hook 经 Service 完成。
import type { ConversationSessionScope } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { create } from 'zustand';
import {
  conversationInitialState,
  type ConversationState,
} from './conversationStoreState';
import {
  clearSessionKeyPatch,
  instanceSelectionSet,
  invalidateFriendBotList,
  invalidateListCaches,
  revisionPatch,
  sessionKeyPatch,
  withoutKey,
} from './conversationStoreHelpers';
import { selectedSnapshotMatches, type SelectedSessionSnapshot } from './conversationStoreSnapshot';

export * from './conversationStoreState';

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
        // 归属切换是实际过滤变化:revision 递增;切回 mine 且记忆范围为 favorite 时清空搜索。
        ...revisionPatch(state, botId),
        ...(effectiveScope === 'favorite' ? clearSessionKeyPatch(state, botId) : {}),
      };
    }),
  setManagedBotScope: (botId, scope) =>
    set((state) => {
      // 生效范围实际变化才算一次过滤变化;缺省 all 的等值提交不递增 revision。
      const effectiveChanged = (state.effectiveScopeByManagedBotId[botId] ?? 'all') !== scope;
      return {
        scopeByManagedBotId: { ...state.scopeByManagedBotId, [botId]: scope },
        effectiveScopeByManagedBotId: { ...state.effectiveScopeByManagedBotId, [botId]: scope },
        // 所选管理 Bot 且当前归属为 mine 时,选中范围同步跟随(AC-7 筛选投影 URL)。
        ...(state.selectedBotId === botId &&
        isManagedConversationSection(state.selectedSection) &&
        state.selectedOrigin === 'mine'
          ? { selectedScope: scope }
          : {}),
        ...(effectiveChanged
          ? {
              ...revisionPatch(state, botId),
              // 进入 favorite 清空已生效搜索;列表失效沿用既有 origin/scope 缓存比对,不额外删除。
              ...(scope === 'favorite' ? clearSessionKeyPatch(state, botId) : {}),
            }
          : {}),
      };
    }),
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
        ...(scope === 'favorite' ? clearSessionKeyPatch(state, botId) : {}),
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
