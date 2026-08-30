import { getAvailableViews } from '@/domain/collaboration/availableViews';
import { create } from 'zustand';
import {
  groupFields,
  initialState,
  rememberLastSession,
  restoreIdentitySelection,
  toggleArrayItem,
  toggleRecordExclusive,
  type WorkspaceState,
} from './workspaceStoreState';

export * from './workspaceStoreState';

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  ...initialState,
  setActiveIdentityId: (v) => set({ activeIdentityId: v }),
  setActiveTargetId: (v) => set({ activeTargetId: v }),
  setSearch: (v) => set({ search: v }),
  setView: (v) => set({ view: v }),
  toggleGroup: (group) => set((state) => ({ collapsedGroups: toggleArrayItem(state.collapsedGroups, group) })),
  setIdentities: (items, activeId) => set({ identities: items, activeIdentityId: activeId }),
  setActiveIdentity: (id) => {
    const state = get();
    const target = id ? state.identities.find((i) => i.id === id) ?? null : null;
    const views = getAvailableViews(target ? { id: target.id, kind: target.kind } : null);
    const currentExpandedBotId = Object.keys(state.expandedBotIds)[0] ?? null;
    const currentExpandedGroupId = Object.keys(state.expandedGroupIds)[0] ?? null;
    const updatedMemo = state.activeIdentityId
      ? rememberLastSession(state.lastSessionByIdentity, state.activeIdentityId, {
          view: state.view,
          selectedGroupId: state.selectedGroupId,
          selectedSessionId: state.selectedSessionId,
          expandedGroupId: currentExpandedGroupId,
          membership: state.membership,
          selectedBotSessionId: state.selectedBotSessionId,
          expandedBotId: currentExpandedBotId,
        })
      : state.lastSessionByIdentity;
    const {
      restoredView,
      selectedGroupId,
      selectedSessionId,
      expandedGroupIds,
      membership,
      selectedBotSessionId,
      expandedBotIds,
      expandedBotSectionKey,
    } = restoreIdentitySelection(updatedMemo, id, views, state.view);
    set({
      activeIdentityId: id ?? null,
      selectedGroupId,
      selectedSessionId,
      selectedBotSessionId,
      groupSearchText: '',
      groupKindFilter: 'all',
      groupSortMode: 'lastActivity',
      membership,
      sessionTabsByGroup: {},
      sessionSearchText: '',
      activePanel: 'none',
      expandedGroupIds,
      expandedBotIds,
      expandedBotSectionKey,
      lastSessionByIdentity: updatedMemo,
      view: restoredView,
    });
  },
  selectGroup: (groupId) => set({ selectedGroupId: groupId, selectedSessionId: null, activePanel: 'none' }),
  markBcsGroup: (id) =>
    set((state) => (state.bcsGroupIds[id] ? state : { bcsGroupIds: { ...state.bcsGroupIds, [id]: true } })),
  selectSession: (sessionId) => set({ selectedSessionId: sessionId }),
  setGroupSearchText: (v) => set({ groupSearchText: v }),
  setGroupKindFilter: (k) => set({ groupKindFilter: k }),
  setGroupSortMode: (m) => set({ groupSortMode: m }),
  setMembership: (m) => set({ membership: m }),
  toggleGroupExpanded: (groupId) =>
    set((state) => ({ expandedGroupIds: toggleRecordExclusive(state.expandedGroupIds, groupId) })),
  toggleBotExpanded: (botId) =>
    set((state) => ({
      expandedBotIds: toggleRecordExclusive(state.expandedBotIds, botId) as Record<string, true>,
      expandedBotSectionKey: state.expandedBotIds[botId]
        ? state.expandedBotSectionKey
        : { ...state.expandedBotSectionKey, [botId]: 'mine' },
      // 切换/收起 Bot 卡片时未选择新会话，旧会话不应继续占用右侧聊天区。
      selectedBotSessionId: null,
    })),
  setBotExpandedSection: (botId, sectionKey) =>
    set((state) => ({ expandedBotSectionKey: { ...state.expandedBotSectionKey, [botId]: sectionKey } })),
  selectBotSession: (sessionId) => set({ selectedBotSessionId: sessionId }),
  bumpHistoryRefresh: () => set((state) => ({ historyRefreshNonce: state.historyRefreshNonce + 1 })),
  setSessionTabForGroup: (groupId, tab) =>
    set((state) => ({ sessionTabsByGroup: { ...state.sessionTabsByGroup, [groupId]: tab } })),
  setSessionSearchText: (v) => set({ sessionSearchText: v }),

  setConnectionState: (s) => set({ connectionState: s }),
  setActivePanel: (p) => set({ activePanel: p }),
  setIsGroupsLoading: (v) => set({ isGroupsLoading: v }),
  setIsSessionsLoading: (v) => set({ isSessionsLoading: v }),
  reset: () => set(initialState),
  resetWorkspace: () => set(groupFields),
}));
