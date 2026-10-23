import { getCapabilities } from '@/capabilities';
import type { GroupPanelKind } from '@/pages/Workspace/components/GroupHeader';
import { filterVisibleGroupMessages } from '@/pages/Workspace/hooks/groupChatHistoryUtils';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import React, { useState } from 'react';
import { GroupChatAbortUnsupportedDialog } from './components/GroupChatAbortUnsupportedDialog';
import { GroupChatPane } from './components/GroupChatPane';
import { GroupHistorySidebar } from './components/GroupChatPane/GroupHistorySidebar';
import { SessionFilesSidebar } from './components/GroupChatPane/SessionFilesSidebar';
import { GroupMembersPanelSlot } from './components/GroupMembersPanelSlot';
import { GroupWorkspaceSidebar } from './components/GroupWorkspaceSidebar';
import { CreateGroupModal } from './components/Modals/CreateGroupModal';
import { ScopedWorkspaceUnavailable } from './components/ScopedWorkspaceUnavailable';
import { WorkspaceManagePanels } from './components/WorkspaceManagePanels';
import type { GroupWorkspaceAreaProps } from './GroupWorkspaceArea.types';
import { useGroupChat } from './hooks/useGroupChat';
import { useGroupCreateDialog } from './hooks/useGroupCreateDialog';
import { useGroupManagement } from './hooks/useGroupManagement';
import { useGroupChatHeaderPanels } from './hooks/useGroupChatHeaderPanels';
import { useGroupSessions } from './hooks/useGroupSessions';
import { useGroupWorkspace } from './hooks/useGroupWorkspace';
import { useGroupSidebarProps } from './hooks/useGroupSidebarProps';
import { useOpenDefaultGroupSession } from './hooks/useOpenDefaultGroupSession';
import { useSessionManagement } from './hooks/useSessionManagement';
export function GroupWorkspaceArea({
  scope,
  sessionOnly = false,
  userAvatarUrl,
  userIdentityId,
  userIdentityName,
  mobileListOpen,
  onCloseMobileList,
  onOpenMobileList,
}: GroupWorkspaceAreaProps) {
  const ws = useGroupWorkspace(scope?.group);
  const expandedGroupIds = React.useMemo(
    () => ws.groups.filter((g) => ws.expandedGroupIds[g.groupId]).map((g) => g.groupId),
    [ws.groups, ws.expandedGroupIds],
  );
  const sessions = useGroupSessions(
    scope && ws.selectedGroupId !== scope.group.groupId ? null : ws.selectedGroupId,
    expandedGroupIds,
    sessionOnly ? scope?.session ?? undefined : undefined,
  );
  const chat = useGroupChat(scope && !ws.selectedGroup ? null : sessions.selectedSession, userIdentityId);
  const [activePanel, setActivePanel] = useState<GroupPanelKind>('none');
  // 头部六图标域（收藏/历史消息/副屏）：搜索口径与消息区渲染同源（filterVisibleGroupMessages），
  // 保证历史面板每条结果都能在消息区定位到（useGroupChatDisplayStatus 同款同源约定）。
  const headerPanels = useGroupChatHeaderPanels({
    session: sessions.selectedSession,
    messages: filterVisibleGroupMessages(chat.chat.messages ?? []),
    panelRef: chat.panelRef,
    toggleFavorite: sessions.toggleFavorite,
  });
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
  // 内流侧栏（≥lg）与 <lg 抽屉共用同一份 props（含仅侧栏消费的 handlers），
  // 装配下沉至 useGroupSidebarProps，避免两处分叉。
  const groupSidebarProps = useGroupSidebarProps({
    restricted: Boolean(scope),
    ws,
    sessions,
    groupManage,
    sessionManage,
    createGroupDialog,
    setActivePanel,
  });
  const { selectedGroup, canManageGroup: canManage } = ws;
  // 管理面板打开期间跟随选中群补拉详情：打开面板（齿轮/「…」菜单）或面板保持打开时切群，
  // 都会为新选中群拉取 participants 等详情；否则列表项 participants 为空且 participantCount>0，
  // 「群成员管理」card 会一直停留在「加载中…」。面板关闭时不拉取（保持按需拉取约定）。
  const { selectedGroupId, reloadSelectedGroup } = ws;
  React.useEffect(() => {
    if (activePanel !== 'manage' || !selectedGroupId) return;
    void reloadSelectedGroup(selectedGroupId);
  }, [activePanel, selectedGroupId, reloadSelectedGroup]);

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
  const handleShareGroup = (expiresInSeconds?: number) =>
    selectedGroup
      ? groupManage.createShare(undefined, expiresInSeconds)
      : Promise.resolve({
          ok: false as const,
          error: { code: 'GROUP_MISSING', friendlyMessage: '未选择协作群', canRetry: false },
        });
  const handleShareSession = (expiresInSeconds?: number) => sessionManage.createShare(undefined, expiresInSeconds);
  const handleTogglePanel = (panel: GroupPanelKind) => setActivePanel(panel);
  if (scope && (!selectedGroup || (sessionOnly && !sessions.selectedSession))) {
    return (
      <ScopedWorkspaceUnavailable
        error={ws.groupsError}
        loading={ws.isLoadingGroups}
        onRetry={() => void ws.retryGroups()}
      />
    );
  }
  return (
    <>
      {!sessionOnly && (
        <GroupWorkspaceSidebar sidebar={groupSidebarProps} open={mobileListOpen} onClose={onCloseMobileList} />
      )}
      <div className="relative isolate flex min-h-0 min-w-0 flex-1" data-workspace-conversation="true">
        <GroupChatPane
          identityLocked={Boolean(scope)}
          group={selectedGroup}
          session={sessions.selectedSession}
          activeIdentity={ws.activeIdentity}
          updateMemberMode={sessions.updateMemberMode}
          updateMemberScope={sessions.updateMemberScope}
          panelRef={chat.panelRef}
          chatBridge={chat.chatBridge}
          chat={chat.chat}
          historyOnly={chat.historyOnly}
          supportState={chat.supportState}
          connectionStatus={chat.connectionStatus}
          questions={chat.questions}
          execApprovals={chat.execApprovals}
          speakingLease={chat.speakingLease}
          groupBootstrapProcessing={chat.groupBootstrapProcessing}
          send={chat.send}
          submitPanelMessage={chat.submitPanelMessage}
          submitTaskExecutionMessage={chat.submitTaskExecutionMessage}
          appendAssistantMessage={chat.appendAssistantMessage}
          streamAssistantMessage={chat.streamAssistantMessage}
          abortBot={chat.abortBot}
          abortingBotIds={chat.abortingBotIds}
          queuedDeliveries={chat.queuedDeliveries}
          processingDeliveries={chat.processingDeliveries}
          cancellingDeliveryIds={chat.cancellingDeliveryIds}
          cancelDelivery={chat.cancelDelivery}
          reconnect={chat.reconnect}
          onOpenSessionList={sessionOnly ? undefined : onOpenMobileList}
          reloadHistory={chat.reloadHistory}
          hasMoreHistory={chat.hasMoreHistory}
          isLoadingMoreHistory={chat.isLoadingMoreHistory}
          onLoadMoreHistory={chat.loadMoreHistory}
          canManageGroup={canManage}
          activePanel={activePanel}
          onTogglePanel={handleTogglePanel}
          favorites={headerPanels.favorites}
          sidePaneOpen={headerPanels.sidePaneOpen}
          sidePaneDisabled={headerPanels.sidePaneDisabled}
          onToggleSidePane={headerPanels.toggleSidePane}
          onSidePaneIsOpenChange={headerPanels.onSidePaneIsOpenChange}
          highlightMessageId={headerPanels.highlightMessageId}
          onRequestDissolve={handleDissolve}
          onRequestShareGroup={handleShareGroup}
          onRequestShareSession={handleShareSession}
          inputRef={chat.inputRef}
          userAvatarUrl={userAvatarUrl}
          userIdentityId={userIdentityId}
          userIdentityName={userIdentityName}
        />
        <GroupChatAbortUnsupportedDialog
          open={Boolean(chat.unsupportedAbortBotId)}
          onClose={chat.dismissAbortUnsupported}
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
          authenticatedUserId={userIdentityId}
          authenticatedUserName={userIdentityName}
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
          /* 会话局部覆盖层：资源管理与单聊侧共用容器。 */
          <SessionFilesSidebar
            sessionId={sessions.selectedSession.sessionId}
            participants={sessions.selectedSession.participants}
            authenticatedUser={userIdentityId ? { userId: userIdentityId, name: userIdentityName } : null}
            onClose={() => setActivePanel('none')}
          />
        )}
        {activePanel === 'history' && sessions.selectedSession && (
          <GroupHistorySidebar
            search={headerPanels.historySearch}
            onLocate={headerPanels.locateMessage}
            onClose={() => setActivePanel('none')}
          />
        )}
      </div>
      <CreateGroupModal
        open={createGroupDialog.open}
        activeIdentity={ws.activeIdentity}
        authenticatedUserId={userIdentityId}
        authenticatedUserName={userIdentityName}
        onClose={createGroupDialog.closeModal}
        onCreated={createGroupDialog.handleCreated}
      />
    </>
  );
}
