import { ChatLayoutPanelSlot } from '@/components/Workspace/ChatLayoutPanelSlot';
import { SidePanelCollapseButton } from '@/components/Workspace/SidePanelCollapseButton';
import { TaskLinkedSession } from '@/components/Workspace/TaskLinkedSession';
import { filterVisibleGroupMessages } from '@/pages/Workspace/hooks/groupChatHistoryUtils';
import { useCollabPanel } from '@/pages/Workspace/hooks/useCollabPanel';
import { useGroupTaskExecution } from '@/pages/Workspace/hooks/useGroupTaskExecution';
import { useMessageAreaSkeleton } from '@/pages/Workspace/hooks/useMessageAreaSkeleton';
import { useMessageEdit } from '@/pages/Workspace/hooks/useMessageEdit';
import { useMessageInteractions } from '@/pages/Workspace/hooks/useMessageInteractions';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { MentionConfig } from '@tc-chat/ui';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { useEffect, useMemo, useState } from 'react';
import { GroupHeader } from '../GroupHeader';
import { ComposerStatusBar } from './ComposerStatusBar';
import { FuseSlot } from './FuseSlot';
import { GroupChatCollabPanel } from './GroupChatCollabPanel';
import { GroupChatComposer } from './GroupChatComposer';
import { GroupChatMessageArea } from './GroupChatMessageArea';
import type { GroupChatPaneProps } from './GroupChatPane.types';
import { GroupChatPlaceholder } from './GroupChatPlaceholder';
import { GroupExecApprovalPanel } from './GroupExecApprovalPanel';
import { GroupQuestionPanel } from './GroupQuestionPanel';
import { buildGroupMentionConfig, isHumanOnlyMention } from './mentionHelpers';
import { resolveSender } from './messageHelpers';
import { useGroupChatPaneActions } from './useGroupChatPaneActions';
export { resolveSender };
export function GroupChatPane(props: GroupChatPaneProps) {
  const {
    group,
    submitPanelMessage,
    submitTaskExecutionMessage,
    appendAssistantMessage = () => {},
    streamAssistantMessage,
    session,
    activeIdentity,
    updateMemberMode,
    updateMemberScope,
    chat,
    questions,
    execApprovals,
    supportState,
    connectionStatus,
    groupBootstrapProcessing = false,
    send,
    abortBot,
    abortingBotIds,
    queuedDeliveries,
    processingDeliveries,
    cancellingDeliveryIds,
    cancelDelivery,
    reconnect,
    onOpenSessionList,
    reloadHistory,
    hasMoreHistory,
    isLoadingMoreHistory,
    onLoadMoreHistory,
    canManageGroup,
    activePanel,
    onTogglePanel,
    favorites,
    sidePaneOpen,
    sidePaneDisabled,
    onToggleSidePane,
    onSidePaneIsOpenChange,
    highlightMessageId,
    onRequestDissolve,
    onRequestShareGroup,
    onRequestShareSession,
    panelRef,
    inputRef,
    chatBridge,
    userAvatarUrl,
    userIdentityId,
    userIdentityName,
  } = props;
  const linkedSession = useTaskSidePanelStore((state) => state.linkedSession);
  const closeLinkedSession = useTaskSidePanelStore((state) => state.closeLinkedSession);

  const collabPanel = useCollabPanel(
    session,
    activeIdentity ?? null,
    updateMemberMode ?? (() => Promise.resolve(false)),
    userIdentityId,
    userIdentityName,
    updateMemberScope,
    props.identityLocked,
  );

  const messages = filterVisibleGroupMessages(chat.messages ?? []);
  const isRequesting = !!chat.isRequesting;
  // 消息区两态强制（Spec AC-2，预览反馈第五轮）：空消息一律骨架屏，确认后的真空会话才显示空态。
  const isLoadingHistory = useMessageAreaSkeleton({ messages, status: connectionStatus });
  const messageInteractions = useMessageInteractions({
    sessionId: session?.sessionId,
    messages,
    isRequesting,
  });
  const mentionConfig: MentionConfig | undefined = useMemo(
    () =>
      activeIdentity?.kind === 'user'
        ? buildGroupMentionConfig(session?.participants ?? [], {
            excludedActorIds: activeIdentity.id ? [activeIdentity.id] : [],
          })
        : undefined,
    [activeIdentity?.id, activeIdentity?.kind, session?.participants],
  );

  const taskExecution = useGroupTaskExecution({
    group,
    session,
    activeIdentity,
    panelRef,
    submitPanelMessage,
    submitTaskExecutionMessage,
    appendAssistantMessage,
    streamAssistantMessage,
  });

  const [draft, setDraft] = useState('');
  const [lastSentMentions, setLastSentMentions] = useState<string[]>([]);
  const { editingMessageId, editMessage, cancelEdit, finishEdit } = useMessageEdit({
    sessionId: session?.sessionId,
    isRequesting,
    onDraftChange: setDraft,
    inputRef,
  });
  useEffect(() => {
    setDraft('');
    setLastSentMentions([]);
    panelRef.current?.closePanelForce();
  }, [session?.sessionId, panelRef]);
  // 引用/解释/发送/空态点名写入/副屏面板动作：同目录 useGroupChatPaneActions 下沉装配。
  const { insertDraft, handlePanelAction, quoteSelectedMessage, explainSelectedMessage, handleGroupSend } =
    useGroupChatPaneActions({
      group,
      session,
      messages,
      interactions: messageInteractions,
      onDraftChange: setDraft,
      onSend: send,
      onLastSentMentionsChange: setLastSentMentions,
      finishEdit,
      inputRef,
      userAvatarUrl,
      userIdentityId,
      userIdentityName,
    });
  if (!group) {
    return <GroupChatPlaceholder onOpenSessionList={onOpenSessionList} />;
  }

  const showError = supportState.phase === 'error';
  const isWaitingForBot = !isHumanOnlyMention(lastSentMentions, session?.participants ?? []);

  // 输入框可见性（与下方 GroupChatComposer 渲染条件同源）：空态行动区仅在可发送视角提供。
  const composerVisible = !!session && activeIdentity?.kind !== 'bot' && !collabPanel.humanAbsentOnly;
  // 帽条可见性与 composer 分离：「退出会话」仅在 human 视角已加入（present）时有意义（同原 LeaveBar）。
  const statusBarVisible = composerVisible && collabPanel.humanJoined && !collabPanel.botActorId;

  return (
    // relative 供副屏右上「收起」悬浮钮绝对定位（SidePanelCollapseButton）。
    <section className="relative flex min-w-0 flex-1 flex-col bg-background">
      <ChatLayout className="min-h-0 flex-1">
        {linkedSession ? (
          <TaskLinkedSession target={linkedSession} userId={userIdentityId ?? undefined} onClose={closeLinkedSession} />
        ) : (
          <>
        <GroupHeader
          selectedGroup={group}
          selectedSession={session}
          connectionStatus={connectionStatus}
          onOpenSessionList={onOpenSessionList}
          canManageGroup={canManageGroup}
          activePanel={activePanel}
          onTogglePanel={onTogglePanel}
          favorites={favorites}
          sidePaneOpen={sidePaneOpen}
          sidePaneDisabled={sidePaneDisabled}
          onToggleSidePane={onToggleSidePane}
          onRequestDissolve={onRequestDissolve}
          onRequestShareGroup={onRequestShareGroup}
          onRequestShareSession={onRequestShareSession}
        />

        <GroupChatMessageArea
          session={session}
          group={group}
          isLoadingHistory={isLoadingHistory && !groupBootstrapProcessing}
          groupBootstrapProcessing={groupBootstrapProcessing}
          showError={showError}
          supportError={supportState.error}
          reloadHistory={reloadHistory}
          messages={messages}
          isBotResponding={isRequesting && isWaitingForBot}
          hasMoreHistory={hasMoreHistory}
          isLoadingMoreHistory={isLoadingMoreHistory}
          onLoadMoreHistory={onLoadMoreHistory}
          highlightMessageId={highlightMessageId}
          interactions={messageInteractions}
          userAvatarUrl={userAvatarUrl}
          userIdentityId={userIdentityId}
          userIdentityName={userIdentityName}
          viewerKind={activeIdentity?.kind ?? null}
          canMention={!!mentionConfig}
          onQuoteSelected={quoteSelectedMessage}
          onExplainSelected={explainSelectedMessage}
          onEditMessage={editMessage}
          onInsertDraft={composerVisible ? insertDraft : undefined}
        />
        <GroupChatCollabPanel
          panel={collabPanel}
          session={session}
          messages={messages}
          abortingBotIds={abortingBotIds}
          abortBot={abortBot}
          queuedDeliveries={queuedDeliveries}
          processingDeliveries={processingDeliveries}
          cancellingDeliveryIds={cancellingDeliveryIds}
          onCancelDelivery={cancelDelivery}
        />
        {/* ask_user 问答面板：仅 Human 视角挂载（Bot 不代答）。不参与 composerVisible 的
            humanAbsentOnly 条件——Human 未加入但其拥有的 Bot 仍在会话内时仍可作答，授权由 BCS 校验。
            Spec：面板位于输入区域上方，不替代、也不随问答待处理禁用普通聊天输入框。 */}
        {session && activeIdentity?.kind === 'user' && execApprovals ? (
          <GroupExecApprovalPanel model={execApprovals} participants={session.participants} />
        ) : null}
        {session && activeIdentity?.kind === 'user' && questions ? (
          <GroupQuestionPanel model={questions} participants={session.participants} />
        ) : null}
        {/* 输入框仅在 human 视角显示：Bot 视角由协作面板控制发言；Human absent 时由「加入」条接管。
            帽条（发言模式/连接状态）与输入卡同层紧贴渲染（design 决策 7/8），与 humanAbsentOnly 互斥；
            横向 padding 收敛到本容器（GroupChatComposer 根节点不再自带 px），保证帽条 w-[calc(100%-26px)]
            以输入卡实际宽度为基准每侧窄 13px，且 py 零残留使帽条紧贴卡片顶。 */}
        {composerVisible ? (
          <div className="flex flex-col px-3 pb-2 sm:px-6 sm:pb-2.5">
            {/* 帽条承载「退出会话」，仅 human 视角已加入（present）时渲染（同原 LeaveBar 可见条件）。 */}
            {statusBarVisible ? (
              <ComposerStatusBar
                connectionStatus={connectionStatus}
                humanName={collabPanel.humanName}
                viewScope={collabPanel.humanViewScope}
                switchingViewScope={collabPanel.switchingViewScope}
                onViewScopeChange={(scope) => void collabPanel.setViewScope(scope)}
                onLeave={() => collabPanel.leaveSession()}
                onReconnect={() => {
                  void reconnect();
                }}
              />
            ) : null}
            <GroupChatComposer
              session={session}
              connectionStatus={connectionStatus}
              mentionConfig={mentionConfig}
              onSend={handleGroupSend}
              draft={draft}
              onDraftChange={setDraft}
              inputRef={inputRef}
              execution={taskExecution}
              quote={messageInteractions.quote}
              onClearQuote={messageInteractions.clearQuote}
              editingMessageId={editingMessageId}
              onCancelEdit={cancelEdit}
            />
          </div>
        ) : null}
          </>
        )}
        {/* 协作群副屏口径：加宽至 480（不动 WORKSPACE_RIGHT_PANEL_LAYOUT 共用宽度）；
            不随点外收起，仅顶栏「副屏」钮/悬浮「收起」钮关闭。 */}
        <ChatLayoutPanelSlot
          ref={panelRef}
          onAction={handlePanelAction}
          bridge={chatBridge}
          panelWidth={480}
          dismissOnOutside={false}
          onOpenStateChange={onSidePaneIsOpenChange}
        />
      </ChatLayout>
      {/* 副屏右上「收起」钮：真实开合态开时悬于副屏 TopBar 右侧，点按关闭。 */}
      {sidePaneOpen && onToggleSidePane ? <SidePanelCollapseButton onClick={onToggleSidePane} /> : null}
      {session && (
        <FuseSlot
          group={group}
          session={session}
          sessionId={session.sessionId}
          viewerName={activeIdentity?.displayName}
        />
      )}
    </section>
  );
}
export type { GroupChatPaneProps } from './GroupChatPane.types';
export default GroupChatPane;
