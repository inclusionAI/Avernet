import { ChatPanel } from '@/components/Workspace/ChatPanel';
import { BotModelSelectorContainer } from '@/components/Workspace/ChatPanel/BotModelSelector';
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import { IconButton } from '@/components/ui';
import { useComposerSend } from '@/hooks/useComposerSend';
import { useMinWidth } from '@/hooks/useMediaQuery';
import { useTaskExecuteFromCard } from '@/hooks/useTaskExecuteFromCard';
import { useTaskExecution } from '@/hooks/useTaskExecution';
import { useWorkspace } from '@/hooks/useWorkspace';
import { AgentCodingGuide } from '@/pages/Workspace/components/AgentCodingGuide';
import { ChatSessionSidebarSlot } from '@/pages/Workspace/components/ChatSessionSidebarSlot';
import { AddFriendModal } from '@/pages/Workspace/components/Modals/AddFriendModal';
import { CreateGroupModal } from '@/pages/Workspace/components/Modals/CreateGroupModal';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import { buildAgentCodingChatPath } from '@/services/workspace';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { resolveUserId } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { ResourceReference } from '@tc-chat/core';
import { PanelLeft } from 'lucide-react';
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { GroupWorkspaceArea } from './GroupWorkspaceArea';
import { useBotSessionFilesFeature } from './hooks/useBotSessionFilesFeature';
import { useChatUrlSync } from './hooks/useChatUrlSync';
import { useWorkspacePage } from './hooks/useWorkspacePage';
const WorkspacePage: React.FC = () => {
  const workspacePage = useWorkspacePage();
  const workspace = useWorkspace();
  const { view, setView } = workspacePage;
  const { availableViews } = workspace;
  const navigate = useNavigate();
  const openCollaborationPermissions = () => navigate('/collaboration-privacy');
  const openAgentCodingBot = (bot: ChatBotView) =>
    navigate(buildAgentCodingChatPath({ botId: bot.botId, spaceId: bot.spaceId, spaceName: bot.spaceName }));
  // 聊天视图「+」打开的共享创建协作群弹窗；创建成功后切到协作群视图并选中该群。
  const [createGroupOpen, setCreateGroupOpen] = useState(false);
  // 添加好友（Bot 广场）弹窗状态，与创建协作群弹窗并列。
  const [addFriendOpen, setAddFriendOpen] = useState(false);
  // <lg 二级会话列表抽屉开关。聊天/协作群两种视图共用同一开关，由当前视图渲染对应抽屉。
  const [mobileListOpen, setMobileListOpen] = useState(false);
  const isDesktop = useMinWidth(1024);
  // 视口回到桌面（≥lg）时收起二级列表抽屉，避免抽屉压住重新出现的内流侧栏。
  useEffect(() => {
    if (isDesktop) setMobileListOpen(false);
  }, [isDesktop]);
  const identities = useWorkspaceStore((s) => s.identities);
  const activeIdentity = identities.find((i) => i.id === workspace.activeIdentityId) ?? null;

  // view 钳制安全网(与 useWorkspace 内同名 effect 双保险,确保页面层不渲染越界 tab)。
  useEffect(() => {
    // 身份加载完成前不钳制，避免把 URL 中的单聊 tab 误改到协作群。
    if (!activeIdentity) return;
    if (availableViews.length === 0) return;
    if (!availableViews.includes(view)) setView(availableViews[0]);
  }, [activeIdentity, availableViews, view, setView]);

  // 真实用户 Bot 单聊：任务发起上下文（owner/session）。测试用户无任务入口。
  const selectedBotSession = workspace.botSessions.selectedSession;
  const taskComposerContext = useMemo<TaskComposerContext | null>(() => {
    if (workspace.isTestUser) return null;
    const ownerBotId = selectedBotSession?.botId ?? workspace.botChatTarget?.id;
    if (!ownerBotId || !workspace.activeIdentityId) return null;
    return {
      sourceType: 'bot',
      ownerUserId: resolveUserId(workspace.activeIdentityId),
      ownerBotId,
      mainSessionId: selectedBotSession?.sessionId,
      mainSessionName: selectedBotSession?.title,
      parentTaskId: null,
    };
  }, [workspace.isTestUser, workspace.botChatTarget, workspace.activeIdentityId, selectedBotSession]);

  const taskExecution = useTaskExecution({
    panelRef: workspace.panelRef,
    context: taskComposerContext,
    submitPanelMessage: workspace.submitPanelMessage,
  });
  // 卡片「执行」按钮拦截：task_ready 点执行 → execute + 本地插 panel 消息 → 副屏持久。
  useTaskExecuteFromCard({
    panelRef: workspace.panelRef,
    context: taskComposerContext,
    submitPanelMessage: workspace.submitPanelMessage,
    appendAssistantMessage: workspace.appendAssistantMessage,
    streamAssistantMessage: workspace.streamAssistantMessage,
  });
  const taskComposerDisabledReason = !taskComposerContext && !workspace.isTestUser ? '请先选择一个 Bot 会话' : null;
  const handleSend = useComposerSend(taskExecution, {
    sendMessage: (content, context) => {
      const botChat = workspace.botChat;
      if (!botChat) return;
      const refs = context?.fileRefs;
      if (refs && refs.length > 0) {
        botChat.send(context?.resolvedContent ?? content, {
          resourceReferences: refs.map(
            (f): ResourceReference => ({ type: 'file', resource_id: f.resource_id, insert_id: f.insert_id }),
          ),
          promptFileRefs: refs.map((f) => ({ resource_id: f.resource_id, insert_id: f.insert_id })),
          fileRefDisplay: refs.map((f) => ({ insert_id: f.insert_id, name: f.display_name })),
        });
      } else {
        botChat.send(content);
      }
    },
    clearDraft: () => workspace.setDraft(''),
  });
  const selectedChatBot =
    [...workspace.chatBots, ...workspace.friendBots].find(
      (b) => b.botId === workspace.botSessions.selectedSession?.botId,
    ) ?? null;
  const fileFeature = useBotSessionFilesFeature(
    selectedChatBot,
    workspace.botSessions.selectedSession,
    workspace.activeIdentityId,
    async () => {
      if (selectedChatBot && workspace.botSessions.selectedSession) {
        await workspace.botSessions.clearContext(selectedChatBot, workspace.botSessions.selectedSession.sessionId);
        workspace.botChat.reloadHistory();
      }
    },
  );
  const taskComposerNode = workspace.isTestUser ? null : (
    <ComposerCapabilitiesMenu
      execution={taskExecution}
      enableWorkflow
      onUpload={fileFeature.openUpload}
      onManageFiles={fileFeature.openFileDrawer}
      disabled={!taskComposerContext}
      disabledReason={taskComposerDisabledReason}
      selectedWorkflow={taskExecution.selectedWorkflow}
      pendingDynamic={taskExecution.pendingDynamic}
      onWorkflowSelected={taskExecution.selectWorkflow}
      onDynamicSelected={taskExecution.selectDynamic}
      onClearSelection={taskExecution.clearSelection}
    />
  );

  const isChatView = view === 'chat';
  const expandedBotId = Object.keys(workspace.expandedBotIds)[0];
  useChatUrlSync(
    isChatView,
    workspace.botSessions.selectedSession?.botId ?? expandedBotId,
    workspace.botSessions.selectedSession?.sessionId,
  );

  const renderChatArea = () => {
    // 测试用户:仅客服会话,ChatPanel 全宽,无侧栏。
    if (workspace.isTestUser) {
      return (
        <ChatPanel
          target={workspace.botChatTarget}
          viewer={workspace.activeIdentity}
          userAvatarUrl={workspace.currentUserAvatarUrl}
          messages={workspace.supportMessages}
          isRequesting={workspace.supportIsRequesting}
          isLoadingMessages={workspace.supportIsLoadingMessages}
          connectionStatus={workspace.supportConnectionStatus}
          retryCount={workspace.supportRetryCount}
          supportState={workspace.supportState}
          draft={workspace.draft}
          panelRef={workspace.panelRef}
          chatBridge={workspace.chatBridge}
          onDraftChange={workspace.setDraft}
          onSend={workspace.sendMessage}
          onStop={workspace.stopReply}
          onReconnect={workspace.reconnect}
          onPanelAction={workspace.handlePanelAction}
          mode="support"
          interactive
          inputRef={workspace.inputRef}
        />
      );
    }
    // 真实用户:BotSessionSidebar + ChatPanel(bot 单聊)。
    const botChat = workspace.botChat;
    return (
      <>
        <ChatSessionSidebarSlot
          workspace={workspace}
          view={view}
          onViewChange={setView}
          availableViews={availableViews}
          mobileListOpen={mobileListOpen}
          onMobileListClose={() => setMobileListOpen(false)}
          onOpenCreateGroup={() => setCreateGroupOpen(true)}
          onOpenAddFriend={() => setAddFriendOpen(true)}
          onOpenPermissions={openCollaborationPermissions}
          userAvatarUrl={workspace.currentUserAvatarUrl}
          onManageBot={(bot) => navigate(`/bot-workshop/detail?type=view&id=${encodeURIComponent(bot.realBotId)}`)}
          onOpenBotWorkshop={() => navigate('/bot-workshop')}
        />
        {workspace.selectedAgentCodingBot ? (
          <AgentCodingGuide bot={workspace.selectedAgentCodingBot} onOpen={openAgentCodingBot} />
        ) : (
          <ChatPanel
            target={workspace.botChatTarget}
            viewer={workspace.activeIdentity}
            userAvatarUrl={workspace.currentUserAvatarUrl}
            messages={botChat.chat.messages}
            isRequesting={botChat.chat.isRequesting}
            isLoadingMessages={botChat.chat.isDefaultMessagesRequesting}
            connectionStatus={botChat.connectionStatus}
            retryCount={botChat.chat.retryCount}
            supportState={botChat.supportState}
            draft={workspace.draft}
            panelRef={workspace.panelRef}
            chatBridge={workspace.chatBridge}
            onDraftChange={workspace.setDraft}
            onSend={handleSend}
            onStop={botChat.stop}
            onReconnect={() => void botChat.reconnect()}
            onPanelAction={workspace.handlePanelAction}
            modelSelector={
              <BotModelSelectorContainer
                chatBots={[...workspace.chatBots, ...workspace.friendBots]}
                session={workspace.botSessions.selectedSession}
                activeIdentityId={workspace.activeIdentityId}
                onSessionModelChange={workspace.botSessions.updateSessionModel}
              />
            }
            taskComposer={taskComposerNode}
            fileChip={fileFeature.fileChip}
            command={fileFeature.command}
            fileToolbar={fileFeature.fileToolbar}
            senderRef={fileFeature.senderRef}
            mode="bot"
            interactive
            inputRef={workspace.inputRef}
          />
        )}
        {fileFeature.featureNode}
      </>
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-10 shrink-0 items-center justify-end border-b border-border px-2 lg:hidden">
        <IconButton
          label="打开会话列表"
          icon={<PanelLeft className="h-5 w-5" />}
          onClick={() => setMobileListOpen(true)}
        />
      </div>
      <div className="flex min-h-0 flex-1">
        {isChatView ? (
          renderChatArea()
        ) : (
          <GroupWorkspaceArea
            view={view}
            onViewChange={setView}
            availableViews={availableViews}
            identities={workspace.identities}
            activeIdentityId={workspace.activeIdentityId}
            onChangeIdentity={workspace.setActiveIdentityId}
            onOpenPermissions={openCollaborationPermissions}
            userAvatarUrl={workspace.currentUserAvatarUrl}
            userIdentityId={workspace.activeIdentityId}
            onAddFriend={() => setAddFriendOpen(true)}
            mobileListOpen={mobileListOpen}
            onCloseMobileList={() => setMobileListOpen(false)}
          />
        )}
      </div>
      <CreateGroupModal
        open={createGroupOpen}
        activeIdentity={activeIdentity}
        onClose={() => setCreateGroupOpen(false)}
        onCreated={(group) => {
          setCreateGroupOpen(false);
          setView('group');
          if (group.initialSessionId && group.initialRun?.state === 'running') {
            useWorkspaceStore.getState().setPendingGroupBootstrap({
              groupId: group.groupId,
              sessionId: group.initialSessionId,
              run: group.initialRun,
            });
          }
          useWorkspaceStore.getState().selectGroup(group.groupId);
          if (group.initialSessionId) {
            const store = useWorkspaceStore.getState();
            if (!store.expandedGroupIds[group.groupId]) store.toggleGroupExpanded(group.groupId);
            useWorkspaceStore.getState().selectSession(group.initialSessionId);
            useWorkspaceStore.getState().bumpHistoryRefresh();
          }
        }}
      />
      <AddFriendModal open={addFriendOpen} activeIdentity={activeIdentity} onClose={() => setAddFriendOpen(false)} />
    </div>
  );
};

export default WorkspacePage;
