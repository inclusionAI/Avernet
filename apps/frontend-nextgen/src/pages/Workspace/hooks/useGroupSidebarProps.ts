// GroupWorkspaceArea 的协作群侧栏 props 装配（内流侧栏 ≥lg 与 <lg 抽屉共用同一份 props）：
// props 对象行数远超主组件红线，连同仅侧栏消费的 handlers（建群/建会话/管理/分享/解散）
// 一并下沉至本 hook；装配语义与主组件内联实现一一对应，无行为变化。
import type { GroupSidebarProps } from '@/pages/Workspace/components/GroupSidebar';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { GroupPanelKind } from '@/pages/Workspace/components/GroupHeader';
import type { useGroupCreateDialog } from './useGroupCreateDialog';
import type { useGroupManagement } from './useGroupManagement';
import type { useGroupSessions } from './useGroupSessions';
import type { useGroupWorkspace } from './useGroupWorkspace';
import type { useSessionManagement } from './useSessionManagement';

interface UseGroupSidebarPropsParams {
  /** 群限流/会话限流进入（scope 非空）时侧栏收敛为受限形态。 */
  restricted: boolean;
  ws: ReturnType<typeof useGroupWorkspace>;
  sessions: ReturnType<typeof useGroupSessions>;
  groupManage: ReturnType<typeof useGroupManagement>;
  sessionManage: ReturnType<typeof useSessionManagement>;
  createGroupDialog: ReturnType<typeof useGroupCreateDialog>;
  /** 面板开合出口：侧栏「…」管理菜单需联动主面板状态机。 */
  setActivePanel: (panel: GroupPanelKind) => void;
}

export function useGroupSidebarProps({
  restricted,
  ws,
  sessions,
  groupManage,
  sessionManage,
  createGroupDialog,
  setActivePanel,
}: UseGroupSidebarPropsParams): GroupSidebarProps {
  const sessionTabsByGroup = useWorkspaceStore((s) => s.sessionTabsByGroup);
  const setSessionTabForGroup = useWorkspaceStore((s) => s.setSessionTabForGroup);

  const handleCreateSession: GroupSidebarProps['onCreateSession'] = (groupId, scope) =>
    void sessions.createSessionIn(groupId, undefined, undefined, scope);
  const handleCreateGroup = () => createGroupDialog.openModal();
  // 侧栏「…」菜单：群管理（选中该群并打开管理面板；详情由主组件 effect 按需补拉）。
  const handleManageGroup = (groupId: string) => {
    ws.onSelectGroup(groupId);
    setActivePanel('manage');
  };
  const handleManageSession = (groupId: string, sessionId: string) => {
    void ws.onSelectGroup(groupId);
    sessions.openSession(groupId, sessionId);
    // 打开管理面板前补拉会话详情（participants），列表接口可能只返回 participant_count。
    void sessionService.getSessionDetail(sessionId).then((res) => {
      if (res.ok) sessions.applySessionUpdate(sessionId, res.data);
    });
    setActivePanel('sessionManage');
  };
  const handleShareGroupFromSidebar = (groupId: string, expiresInSeconds?: number) => {
    ws.onSelectGroup(groupId);
    return groupManage.createShare(groupId, expiresInSeconds);
  };
  const handleDissolveGroupFromSidebar = (groupId: string) => void ws.dissolveGroup(groupId);

  return {
    restricted,
    // 身份未加载完成（null）时按 human 兜底展示视角菜单。
    viewerKind: ws.activeIdentity?.kind === 'bot' ? 'bot' : 'user',
    groups: ws.groups,
    isLoading: ws.isLoadingGroups,
    groupsError: ws.groupsError,
    onRetryGroups: ws.retryGroups,
    onSelectGroup: (id) => void ws.onSelectGroup(id),
    groupSearchText: ws.groupSearchText,
    onSearchTextChange: ws.setGroupSearchText,
    kindFilter: ws.kindFilter,
    onKindFilterChange: ws.setKindFilter,
    membership: ws.membership,
    onMembershipChange: ws.setMembership,
    sortMode: ws.sortMode,
    onSortModeChange: ws.setSortMode,
    expandedGroupIds: ws.expandedGroupIds,
    onToggleGroupExpanded: ws.toggleGroupExpanded,
    sessionsByGroupId: sessions.sessionsByGroupId,
    hasMoreSessionsByGroupId: sessions.hasMoreSessionsByGroupId,
    totalSessionsByGroupId: sessions.totalSessionsByGroupId,
    isLoadingMoreSessionsByGroupId: sessions.isLoadingMoreSessionsByGroupId,
    onLoadMoreSessions: sessions.loadMoreSessions,
    errorByGroupId: sessions.errorByGroupId,
    loadMoreErrorByGroupId: sessions.loadMoreErrorByGroupId,
    onReloadSession: sessions.reloadGroup,
    sessionTabsByGroup,
    onSessionTabForGroup: setSessionTabForGroup,
    favoriteSessionIds: sessions.favoriteSessionIds,
    sessionSearchText: sessions.sessionSearchText,
    onSessionSearchTextChange: sessions.setSessionSearchText,
    selectedGroupId: ws.selectedGroupId,
    selectedSessionId: sessions.selectedSessionId,
    onSelectSession: (gid, id) => sessions.openSession(gid, id),
    onCreateSession: handleCreateSession,
    onToggleFavorite: sessions.toggleFavorite,
    onClearSessionFilter: () => sessions.setSessionSearchText(''),
    onCreateGroup: handleCreateGroup,
    onManageGroup: handleManageGroup,
    onManageSession: handleManageSession,
    onRenameSession: sessions.renameSession,
    onDeleteSession: sessions.deleteSession,
    onShareSession: sessionManage.createShare,
    activeIdentity: ws.activeIdentity,
    onShareGroup: handleShareGroupFromSidebar,
    onDissolveGroup: handleDissolveGroupFromSidebar,
  };
}