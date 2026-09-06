import { getCapabilities } from '@/capabilities';
import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import type { GroupPanelKind } from '@/pages/Workspace/components/GroupHeader';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import React, { useState } from 'react';
import { GroupChatPane } from './components/GroupChatPane';
import { SessionFilesModal } from './components/GroupChatPane/SessionFilesModal';
import { GroupMembersPanelSlot } from './components/GroupMembersPanelSlot';
import { GroupSidebar, GroupSidebarList, type GroupSidebarProps } from './components/GroupSidebar';
import { CreateGroupModal } from './components/Modals/CreateGroupModal';
import { WorkspaceManagePanels } from './components/WorkspaceManagePanels';
import { useGroupChat } from './hooks/useGroupChat';
import { useGroupCreateDialog } from './hooks/useGroupCreateDialog';
import { useGroupManagement } from './hooks/useGroupManagement';
import { useGroupSessions } from './hooks/useGroupSessions';
import { useGroupWorkspace } from './hooks/useGroupWorkspace';
import { useOpenDefaultGroupSession } from './hooks/useOpenDefaultGroupSession';
import { useSessionManagement } from './hooks/useSessionManagement';

export function GroupWorkspaceArea({
  view,
  onViewChange,
  availableViews,
  userAvatarUrl,
  userIdentityId,
  mobileListOpen,
  onCloseMobileList,
}: {
  view: 'chat' | 'group';
  onViewChange: (v: 'chat' | 'group') => void;
  availableViews: WorkspaceView[];
  userAvatarUrl?: string;
  userIdentityId?: string | null;
  /** <lg 二级协作群列表抽屉开关（由 Workspace 持有，聊天/协作群视图共用同一开关）。 */
  mobileListOpen: boolean;
  onCloseMobileList: () => void;
}) {
  const ws = useGroupWorkspace();
  const expandedGroupIds = React.useMemo(
    () => ws.groups.filter((g) => ws.expandedGroupIds[g.groupId]).map((g) => g.groupId),
    [ws.groups, ws.expandedGroupIds],
  );
  const sessions = useGroupSessions(ws.selectedGroupId, expandedGroupIds);
  const chat = useGroupChat(sessions.selectedSession);
  const [activePanel, setActivePanel] = useState<GroupPanelKind>('none');
  const groupAdvancedConfigEnabled = getCapabilities().getGroupAdvancedConfigEnabled().value;
  const groupManage = useGroupManagement(
    ws.selectedGroupId,
    ws.reloadSelectedGroup,
    activePanel === 'manage' && groupAdvancedConfigEnabled,
  );
  const sessionManage = useSessionManagement(sessions.selectedSession, sessions.applySessionUpdate);
  const createGroupDialog = useGroupCreateDialog({
    refreshGroups: ws.refreshGroups,
    selectGroup: (id) => ws.onSelectGroup(id),
    openSessionForGroup: useOpenDefaultGroupSession(),
  });
  const sessionTabsByGroup = useWorkspaceStore((s) => s.sessionTabsByGroup);
  const setSessionTabForGroup = useWorkspaceStore((s) => s.setSessionTabForGroup);

  const selectedGroup = ws.selectedGroup;
  const canManage = ws.canManageGroup;

  const handleDissolve = () => {
    if (!ws.selectedGroupId) return;
    void ws.dissolveGroup(ws.selectedGroupId);
  };
  const handleLeaveGroup = async (actorId: string) => {
    const ok = await groupManage.leaveGroup(actorId);
    if (!ok) return false;
    setActivePanel('none');
    await ws.refreshGroups();
    return true;
  };
  const handleLeaveSession = async (actorId: string) => {
    if (!sessions.selectedSession) return false;
    const { sessionId, groupId } = sessions.selectedSession;
    // 捕获退出前的会话数（selectedGroup.sessions 由 loadGroupDetail 填充）。
    const sessionCountBefore = selectedGroup?.sessions.length ?? 0;
    // 会话成员身份以 store.membership 为准（loadGroupDetail 可能尚未回填 group.membership）。
    const isSessionOnly = useWorkspaceStore.getState().membership === 'session_only';
    const ok = await sessions.leaveSession(sessionId, actorId);
    if (ok) {
      setActivePanel('none');
      // 退出后该群已无剩余会话且当前角色仅为「会话成员」→ 刷新群列表，后端将不再返回此群。
      if (isSessionOnly && sessionCountBefore <= 1) {
        useWorkspaceStore.getState().selectGroup(null);
        void ws.refreshGroups();
      } else if (isSessionOnly && groupId) {
        // 仍为会话成员但还有其他会话，仅刷新该群详情。
        void ws.reloadSelectedGroup();
      }
    }
    return ok;
  };
  const handleShareGroup = () =>
    selectedGroup
      ? groupManage.createShare()
      : Promise.resolve({
          ok: false as const,
          error: { code: 'GROUP_MISSING', friendlyMessage: '未选择协作群', canRetry: false },
        });
  const handleShareSession = () => sessionManage.createShare();
  const handleCreateSession = (groupId: string) => void sessions.createSessionIn(groupId);
  const handleCreateGroup = () => createGroupDialog.openModal();
  // 侧栏「…」菜单：群管理（选中该群并打开管理面板，按需拉取群详情）。
  const handleManageGroup = (groupId: string) => {
    ws.onSelectGroup(groupId);
    setActivePanel('manage');
    // 列表项不含 participants/owner/driver，打开管理（查看/编辑）时补齐群详情。
    void ws.reloadSelectedGroup(groupId);
  };
  // 头部齿轮切面板：打开群管理时按需拉取当前选中群详情。
  const handleTogglePanel = (panel: GroupPanelKind) => {
    if (panel === 'manage' && activePanel !== 'manage' && ws.selectedGroupId) {
      void ws.reloadSelectedGroup(ws.selectedGroupId);
    }
    setActivePanel(panel);
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
  const handleShareGroupFromSidebar = (groupId: string) => {
    ws.onSelectGroup(groupId);
    return groupManage.createShare(groupId);
  };
  const handleDissolveGroupFromSidebar = (groupId: string) => void ws.dissolveGroup(groupId);
  // 内流侧栏（≥lg）与 <lg 抽屉共用同一份 props，避免两处分叉。抽屉内选中会话后追加收起。
  const groupSidebarProps: GroupSidebarProps = {
    view,
    onViewChange,
    availableViews,
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
    onShareGroup: handleShareGroupFromSidebar,
    onDissolveGroup: handleDissolveGroupFromSidebar,
  };
  return (
    <>
      <GroupSidebar {...groupSidebarProps} />
      {/* <lg 二级协作群列表抽屉：≥lg 内流侧栏可见，<lg 由移动端顶部的「打开会话列表」按钮触发。 */}
      <Drawer
        open={mobileListOpen}
        onOpenChange={(open) => {
          if (!open) onCloseMobileList();
        }}
      >
        <DrawerContent side="left" size="sm" showClose={false} bodyClassName="p-0 flex flex-col">
          <DrawerTitle className="sr-only">协作群列表</DrawerTitle>
          <GroupSidebarList
            {...groupSidebarProps}
            onSelectSession={(gid, id) => {
              sessions.openSession(gid, id);
              onCloseMobileList();
            }}
          />
        </DrawerContent>
      </Drawer>
      <GroupChatPane
        group={selectedGroup}
        session={sessions.selectedSession}
        activeIdentity={ws.activeIdentity}
        updateMemberMode={sessions.updateMemberMode}
        panelRef={chat.panelRef}
        chatBridge={chat.chatBridge}
        chat={chat.chat}
        supportState={chat.supportState}
        connectionStatus={chat.connectionStatus}
        groupBootstrapProcessing={chat.groupBootstrapProcessing}
        send={chat.send}
        submitPanelMessage={chat.submitPanelMessage}
        appendAssistantMessage={chat.appendAssistantMessage}
        streamAssistantMessage={chat.streamAssistantMessage}
        stop={chat.stop}
        reconnect={chat.reconnect}
        reloadHistory={chat.reloadHistory}
        hasMoreHistory={chat.hasMoreHistory}
        isLoadingMoreHistory={chat.isLoadingMoreHistory}
        onLoadMoreHistory={chat.loadMoreHistory}
        canManageGroup={canManage}
        activePanel={activePanel}
        onTogglePanel={handleTogglePanel}
        onRequestDissolve={handleDissolve}
        onRequestShareGroup={handleShareGroup}
        onRequestShareSession={handleShareSession}
        inputRef={chat.inputRef}
        userAvatarUrl={userAvatarUrl}
        userIdentityId={userIdentityId}
      />
      {selectedGroup ? (
        <GroupMembersPanelSlot
          open={activePanel === 'members'}
          group={selectedGroup}
          session={sessions.selectedSession}
          canManage={canManage}
          onClose={() => setActivePanel('none')}
        />
      ) : null}
      <WorkspaceManagePanels
        activePanel={activePanel}
        group={selectedGroup}
        session={sessions.selectedSession}
        groupAdvancedConfigEnabled={groupAdvancedConfigEnabled}
        canManage={canManage}
        identities={ws.identities}
        activeIdentity={ws.activeIdentity}
        onClose={() => setActivePanel('none')}
        onUpdateGroup={groupManage.updateGroup}
        onDissolveGroup={handleDissolve}
        onLeaveGroup={handleLeaveGroup}
        onAddGroupMember={groupManage.addMember}
        onRemoveGroupMember={groupManage.removeMember}
        onShareGroup={groupManage.createShare}
        onSaveDingTalk={groupManage.saveDingTalk}
        onToggleDingTalkActive={groupManage.toggleDingTalkActive}
        onDeleteDingTalk={groupManage.deleteDingTalk}
        dingTalkBinding={groupManage.dingTalkBinding}
        dingTalkLoading={groupManage.dingTalkLoading}
        onRenameSession={sessions.renameSession}
        onDeleteSession={sessions.deleteSession}
        onLeaveSession={handleLeaveSession}
        onAddSessionMember={sessionManage.addMember}
        onRemoveSessionMember={sessionManage.removeMember}
        onShareSession={sessionManage.createShare}
      />
      {activePanel === 'resources' && sessions.selectedSession && (
        <SessionFilesModal
          sessionId={sessions.selectedSession.sessionId}
          sessionName={sessions.selectedSession.title}
          onClose={() => setActivePanel('none')}
        />
      )}
      <CreateGroupModal
        open={createGroupDialog.open}
        activeIdentity={ws.activeIdentity}
        onClose={createGroupDialog.closeModal}
        onCreated={createGroupDialog.handleCreated}
      />
    </>
  );
}
