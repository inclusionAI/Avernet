import { getCapabilities } from '@/capabilities';
import type { GroupPanelKind } from '@/pages/Workspace/components/GroupHeader';
import { filterVisibleGroupMessages } from '@/pages/Workspace/hooks/groupChatHistoryUtils';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import React, { useCallback, useState } from 'react';
import { GroupChatAbortUnsupportedDialog } from './components/GroupChatAbortUnsupportedDialog';
import { GroupChatPane } from './components/GroupChatPane';
import { GroupHistorySidebar } from './components/GroupChatPane/GroupHistorySidebar';
import { SessionFilesSidebar } from './components/GroupChatPane/SessionFilesSidebar';
import { GroupMembersPanelSlot } from './components/GroupMembersPanelSlot';
import { GroupWorkspaceSidebar } from './components/GroupWorkspaceSidebar';
import { GroupWorkspaceConversation } from './components/GroupWorkspaceConversation';
import { CreateGroupModal } from './components/Modals/CreateGroupModal';
import { ScopedWorkspaceUnavailable } from './components/ScopedWorkspaceUnavailable';
import { WorkspaceManagePanels } from './components/WorkspaceManagePanels';
import type { GroupWorkspaceAreaProps } from './GroupWorkspaceArea.types';
import { useGroupChat } from './hooks/useGroupChat';
import { useGroupCreateDialog } from './hooks/useGroupCreateDialog';
import { useGroupManagement } from './hooks/useGroupManagement';
import { useGroupChatHeaderPanels } from './hooks/useGroupChatHeaderPanels';
import { useGroupSessionLocation } from './hooks/useGroupSessionLocation';
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
    // 数据加载只跟随显式展开意图，不把搜索/类型筛选的显隐当成重新展开。
    () => Object.keys(ws.expandedGroupIds).filter((id) => !scope || id === scope.group.groupId),
    [ws.expandedGroupIds, scope],
  );
  const sessions = useGroupSessions(
    scope && ws.selectedGroupId !== scope.group.groupId ? null : ws.selectedGroupId,
    expandedGroupIds,
    sessionOnly ? scope?.session ?? undefined : undefined,
  );
  const chat = useGroupChat(scope && !ws.selectedGroup ? null : sessions.selectedSession, userIdentityId);
  const [activePanel, setActivePanel] = useState<GroupPanelKind>('none');
  // 右缘域面板（管理/历史/资源）全收出口：面板形态与 chat 同构（内联副屏），与悬浮副屏互斥——
  // 关合/让位回开统一经 useGroupChatHeaderPanels 内的 sidePanePanelMutex 编排。
  const closeHeaderPanels = useCallback(() => setActivePanel('none'), []);
  const sessionLocation = useGroupSessionLocation(ws.selectedGroup, sessions.selectedSession, onOpenMobileList);
  // 头部六图标域（收藏/历史消息/副屏）：搜索口径与消息区渲染同源（filterVisibleGroupMessages），
  // 保证历史面板每条结果都能在消息区定位到（useGroupChatDisplayStatus 同款同源约定）。
  const headerPanels = useGroupChatHeaderPanels({
    session: sessions.selectedSession,
    userId: userIdentityId ?? null,
    messages: filterVisibleGroupMessages(chat.chat.messages ?? []),
    panelRef: chat.panelRef,
    toggleFavorite: sessions.toggleFavorite,
    panelsOpen: activePanel !== 'none',
    closePanels: closeHeaderPanels,
  });
  const { dismissSidePaneForPanels } = headerPanels;
  const openPanelKind = useCallback(
    (panel: GroupPanelKind) => {
      // 右缘域面板即将展开（侧栏「…」菜单/头部图标）：副屏让位收起（closePanel 保留 tabs，
      // 面板全收后互斥 Hook 自动回开）——chat 侧 chat-header-panels 同构互斥。
      if (panel !== 'none') dismissSidePaneForPanels();
      setActivePanel(panel);
    },
    [dismissSidePaneForPanels],
  );
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
    // 侧栏「…」菜单开面板与头部图标同语义：经 openPanelKind 让位副屏后再展开。
    setActivePanel: openPanelKind,
  });
  const { selectedGroup, canManageGroup: canManage } = ws;
  // 管理面板打开期间按需补齐完整群详情：打开/切群都刷新；同群被新列表对象替换
  // （participantDetailsLoaded=false）也会重拉，详情回填（true，含显式空详情）后停止。
  // 面板关闭时不拉取，保留头像补拉的请求预算（AC-02）。
  const { selectedGroupId, reloadSelectedGroup } = ws;
  const lastManagedGroupRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (activePanel !== 'manage' || !selectedGroupId) {
      lastManagedGroupRef.current = null;
      return;
    }
    const entering = lastManagedGroupRef.current !== selectedGroupId;
    lastManagedGroupRef.current = selectedGroupId;
    const detailLoaded = selectedGroup?.participantDetailsLoaded ??
      ((selectedGroup?.participants.length ?? 0) > 0);
    if (entering || !detailLoaded) void reloadSelectedGroup(selectedGroupId);
  }, [activePanel, selectedGroupId, selectedGroup, reloadSelectedGroup]);

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
  const handleTogglePanel = (panel: GroupPanelKind) => openPanelKind(panel);
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
        <GroupWorkspaceSidebar
          sidebar={{ ...groupSidebarProps, location: sessionLocation.location, onDismissLocation: sessionLocation.dismissLocation }}
          open={mobileListOpen}
          onClose={onCloseMobileList}
        />
      )}
      <GroupWorkspaceConversation workspace={ws}>
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
          onLocateSession={sessionOnly ? undefined : sessionLocation.locate}
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
            onClose={closeHeaderPanels}
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
          onClose={closeHeaderPanels}
          onUpdateGroup={groupManage.updateGroup}
          onDissolveGroup={handleDissolve}
          onLeaveGroup={handleLeaveGroup}
          onAddGroupMember={groupManage.addMember}
          onRemoveGroupMember={groupManage.removeMember}
          onShareGroup={handleShareGroup}
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
          onShareSession={handleShareSession}
        />
        {sessions.selectedSession ? (
          <>
            {/* 资源管理/历史消息：对话区内联副屏（open 开合、宽度记忆），与单聊侧同形。 */}
            <SessionFilesSidebar
              open={activePanel === 'resources'}
              sessionId={sessions.selectedSession.sessionId}
              participants={sessions.selectedSession.participants}
              authenticatedUser={userIdentityId ? { userId: userIdentityId, name: userIdentityName } : null}
              onClose={closeHeaderPanels}
            />
            <GroupHistorySidebar
              open={activePanel === 'history'}
              search={headerPanels.historySearch}
              onLocate={headerPanels.locateMessage}
              onClose={closeHeaderPanels}
            />
          </>
        ) : null}
      </GroupWorkspaceConversation>
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
