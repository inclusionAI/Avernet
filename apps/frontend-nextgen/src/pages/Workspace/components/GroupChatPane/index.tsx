import { Button, Empty } from '@/components/ui';
import { ChatMessageSkeleton } from '@/components/Workspace/ChatPanel/ChatMessageSkeleton';
import { SidePanelCollapseButton } from '@/components/Workspace/SidePanelCollapseButton';
import { filterVisibleGroupMessages } from '@/pages/Workspace/hooks/groupChatHistoryUtils';
import { useCollabPanel } from '@/pages/Workspace/hooks/useCollabPanel';
import { useGroupTaskExecution } from '@/pages/Workspace/hooks/useGroupTaskExecution';
import { useMessageAreaSkeleton } from '@/pages/Workspace/hooks/useMessageAreaSkeleton';
import { useMessageEdit } from '@/pages/Workspace/hooks/useMessageEdit';
import { buildExplainPrompt, useMessageInteractions } from '@/pages/Workspace/hooks/useMessageInteractions';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import type { PanelAction } from '@tc-chat/core';
import type { MentionConfig } from '@tc-chat/ui';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { RefreshCw, Sparkles } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { GroupHeader } from '../GroupHeader';
import { FuseSlot } from './FuseSlot';
import { GroupChatCollabPanel } from './GroupChatCollabPanel';
import { GroupChatComposer } from './GroupChatComposer';
import { ComposerStatusBar } from './ComposerStatusBar';
import { GroupChatMessageList } from './GroupChatMessageList';
import type { GroupChatPaneProps } from './GroupChatPane.types';
import { buildGroupMentionConfig, isHumanOnlyMention } from './mentionHelpers';
import { resolveSender } from './messageHelpers';
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
  // 空态点名卡落地（对齐单聊 ChatEmptyState 的 insertDraft）：写入 '@' 并聚焦——Sender 的
  // insert 走 commitValue→mentionHandlers.onValueChange，成员面板随之自然唤起。
  const insertDraft = useCallback(
    (text: string) => {
      inputRef?.current?.insert(text);
      inputRef?.current?.focus();
    },
    [inputRef],
  );
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
  const handlePanelAction = (action: PanelAction) => {
    if (action.type === 'fill_input') {
      setDraft(action.content);
      return;
    }
    send(action.content);
  };
  const quoteSelectedMessage = (text: string) => {
    const selectedMessage = messages.find((message) => message.id === messageInteractions.selection?.messageId);
    if (!selectedMessage) return;
    const sender = resolveSender(
      selectedMessage,
      group,
      session?.participants,
      userAvatarUrl,
      userIdentityId,
      userIdentityName,
    );
    messageInteractions.quoteMessage(selectedMessage.id, sender?.name ?? '未命名成员', text);
  };

  const explainSelectedMessage = (text: string) => {
    const selectedMessage = messages.find((message) => message.id === messageInteractions.selection?.messageId);
    if (!selectedMessage) return;
    const sender = resolveSender(
      selectedMessage,
      group,
      session?.participants,
      userAvatarUrl,
      userIdentityId,
      userIdentityName,
    );
    setDraft(buildExplainPrompt(sender?.name ?? '未命名成员', text));
    messageInteractions.clearQuote();
    messageInteractions.setSelection(null);
    finishEdit();
    inputRef?.current?.focus();
  };
  const handleGroupSend = (content: string, mentions?: string[], attachments?: SessionMessageAttachment[]) => {
    messageInteractions.markRead();
    messageInteractions.clearQuote();
    finishEdit();
    setLastSentMentions(mentions ?? []);
    send(content, mentions, attachments);
  };
  if (!group) {
    return (
      <section className="flex min-w-0 flex-1 items-center justify-center bg-background">
        <Empty
          title="欢迎进入协作群对话现场"
          description="在左侧选择一个协作群，开始与多个 Bot 和用户协同。"
          icon={<Sparkles className="h-5 w-5" />}
        />
      </section>
    );
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
        <GroupHeader
          selectedGroup={group}
          selectedSession={session}
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

        {!session ? (
          <div className="flex min-h-0 flex-1 items-center justify-center px-3 py-3 sm:px-6 sm:py-4">
            <Empty
              title="请选择或创建一个会话"
              description="从左侧选择一个会话，或为当前协作群创建新会话后再发送消息。"
              icon={<Sparkles className="h-5 w-5" />}
            />
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col bg-background">
            {isLoadingHistory && !groupBootstrapProcessing ? (
              /* 消息区加载骨架：复用对话页「誊写稿」占位（协作群 BubbleList 与 Bot 面板同用
                  MessageSenderLayout，行几何同构）；画布 padding 对齐 BubbleList，真消息换入零横跳。 */
              <ChatMessageSkeleton
                ariaLabel="加载协作群会话历史"
                paddingClassName="px-3 py-3 sm:px-6 sm:py-4"
              />
            ) : showError ? (
              <div className="flex min-h-0 flex-1 items-center justify-center px-3 py-3 sm:px-6 sm:py-4">
                <Empty
                  title="加载会话历史失败"
                  description={supportState.error ?? '协作群连接出现异常，请重试。'}
                  action={
                    <Button
                      size="sm"
                      leftIcon={<RefreshCw className="h-3.5 w-3.5" />}
                      onClick={() => {
                        void reloadHistory();
                      }}
                    >
                      重新加载历史
                    </Button>
                  }
                />
              </div>
            ) : (
              <GroupChatMessageList
                messages={messages}
                group={group}
                session={session}
                isRequesting={isRequesting && isWaitingForBot}
                groupBootstrapProcessing={groupBootstrapProcessing}
                hasMoreHistory={hasMoreHistory}
                isLoadingMoreHistory={isLoadingMoreHistory}
                onLoadMoreHistory={onLoadMoreHistory}
                highlightMessageId={highlightMessageId}
                interactions={messageInteractions}
                userAvatarUrl={userAvatarUrl}
                userIdentityId={userIdentityId}
                userIdentityName={userIdentityName}
                onQuoteSelected={quoteSelectedMessage}
                onExplainSelected={explainSelectedMessage}
                onEditMessage={editMessage}
                viewerKind={activeIdentity?.kind ?? null}
                canMention={!!mentionConfig}
                onInsertDraft={composerVisible ? insertDraft : undefined}
              />
            )}
          </div>
        )}
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
        <ChatLayout.Panel ref={panelRef} onAction={handlePanelAction} bridge={chatBridge} />
      </ChatLayout>
      {/* 副屏右上「收起」钮：镜像态开时悬于副屏 TopBar 右侧，点按关闭并同步镜像。 */}
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
