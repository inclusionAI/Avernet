import { Button, Empty, Skeleton } from '@/components/ui';
import { useTaskExecuteFromCard } from '@/hooks/useTaskExecuteFromCard';
import { useTaskExecution } from '@/hooks/useTaskExecution';
import { useCollabPanel } from '@/pages/Workspace/hooks/useCollabPanel';
import { useMessageEdit } from '@/pages/Workspace/hooks/useMessageEdit';
import { buildExplainPrompt, useMessageInteractions } from '@/pages/Workspace/hooks/useMessageInteractions';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import { resolveUserId } from '@/services/workspace/botSessionService';
import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import type { PanelAction } from '@tc-chat/core';
import type { MentionConfig } from '@tc-chat/ui';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { RefreshCw, Sparkles } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { GroupHeader } from '../GroupHeader';
import { CollabPanel } from './CollabPanel';
import { FuseSlot } from './FuseSlot';
import { GroupChatComposer } from './GroupChatComposer';
import { GroupChatMessageList } from './GroupChatMessageList';
import type { GroupChatPaneProps } from './GroupChatPane.types';
import { buildGroupMentionConfig } from './mentionHelpers';
import { resolveSender } from './messageHelpers';
export { resolveSender };
export function GroupChatPane(props: GroupChatPaneProps) {
  const {
    group,
    submitPanelMessage,
    appendAssistantMessage = () => {},
    streamAssistantMessage,
    session,
    activeIdentity,
    updateMemberMode,
    chat,
    supportState,
    connectionStatus,
    groupBootstrapProcessing = false,
    send,
    stop,
    reconnect,
    reloadHistory,
    hasMoreHistory,
    isLoadingMoreHistory,
    onLoadMoreHistory,
    canManageGroup,
    activePanel,
    onTogglePanel,
    onRequestDissolve,
    onRequestShareGroup,
    onRequestShareSession,
    panelRef,
    inputRef,
    chatBridge,
    userAvatarUrl,
    userIdentityId,
  } = props;

  const navigate = useNavigate();
  const openCollaborationPermissions = () => navigate('/collaboration-privacy');

  const collabPanel = useCollabPanel(
    session,
    activeIdentity ?? null,
    updateMemberMode ?? (() => Promise.resolve(false)),
  );

  const messages = (chat.messages ?? []).filter(
    (m) => !(m.role === 'assistant' && m.status === 'pending' && !m.content && !m.blocks?.length),
  );
  const isRequesting = !!chat.isRequesting;
  const isLoadingHistory = supportState.phase === 'loading-history' || supportState.phase === 'preparing';
  const messageInteractions = useMessageInteractions({
    sessionId: session?.sessionId,
    messages,
    isRequesting,
    onStop: stop,
  });
  const mentionConfig: MentionConfig | undefined = useMemo(
    () => (activeIdentity?.kind === 'user' ? buildGroupMentionConfig(session?.participants ?? []) : undefined),
    [activeIdentity?.kind, session?.participants],
  );

  const taskComposerContext = useMemo<TaskComposerContext | null>(() => {
    if (!group || !session || activeIdentity?.kind !== 'user') return null;
    const ownerBot = group.participants.find((p) => p.kind === 'bot');
    if (!ownerBot || !activeIdentity?.id) return null;
    return {
      sourceType: 'coop_group',
      ownerUserId: resolveUserId(activeIdentity.id),
      ownerBotId: ownerBot.actorId,
      mainSessionId: session.sessionId,
      mainSessionName: session.title,
      sourceGroupId: group.groupId,
      parentTaskId: null,
    };
  }, [group, session, activeIdentity]);

  const taskExecution = useTaskExecution({ panelRef, context: taskComposerContext, submitPanelMessage });
  useTaskExecuteFromCard({
    panelRef,
    context: taskComposerContext,
    submitPanelMessage,
    appendAssistantMessage,
    streamAssistantMessage,
    onOpenCollaborationPermissions: openCollaborationPermissions,
  });

  const [draft, setDraft] = useState('');
  const { editingMessageId, editMessage, cancelEdit, finishEdit } = useMessageEdit({
    sessionId: session?.sessionId,
    isRequesting,
    onDraftChange: setDraft,
    inputRef,
  });
  useEffect(() => {
    setDraft('');
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
    const sender = resolveSender(selectedMessage, group, session?.participants, userAvatarUrl, userIdentityId);
    messageInteractions.quoteMessage(selectedMessage.id, sender?.name ?? '未命名成员', text);
  };

  const explainSelectedMessage = (text: string) => {
    const selectedMessage = messages.find((message) => message.id === messageInteractions.selection?.messageId);
    if (!selectedMessage) return;
    const sender = resolveSender(selectedMessage, group, session?.participants, userAvatarUrl, userIdentityId);
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
  const showReconnectToolbar =
    connectionStatus === 'error' || connectionStatus === 'disconnected' || connectionStatus === 'reconnecting';

  return (
    <section className="flex min-w-0 flex-1 flex-col bg-background">
      <ChatLayout className="min-h-0 flex-1">
        <GroupHeader
          selectedGroup={group}
          selectedSession={session}
          supportState={supportState}
          connectionStatus={connectionStatus}
          onReconnect={() => {
            void reconnect();
          }}
          canManageGroup={canManageGroup}
          activePanel={activePanel}
          onTogglePanel={onTogglePanel}
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
              <div className="flex min-h-0 flex-1 flex-col space-y-3 px-3 py-6 sm:px-6" aria-label="加载协作群会话历史">
                <Skeleton.Block className="h-12 w-3/4 rounded-xl" />
                <Skeleton.Block className="h-12 w-2/3 rounded-xl" />
                <Skeleton.Block className="h-12 w-5/6 rounded-xl" />
              </div>
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
                isRequesting={isRequesting}
                groupBootstrapProcessing={groupBootstrapProcessing}
                hasMoreHistory={hasMoreHistory}
                isLoadingMoreHistory={isLoadingMoreHistory}
                onLoadMoreHistory={onLoadMoreHistory}
                interactions={messageInteractions}
                userAvatarUrl={userAvatarUrl}
                userIdentityId={userIdentityId}
                onQuoteSelected={quoteSelectedMessage}
                onExplainSelected={explainSelectedMessage}
                onEditMessage={editMessage}
                onStop={stop}
              />
            )}
          </div>
        )}
        <CollabPanel panel={collabPanel} />

        {session && activeIdentity?.kind !== 'bot' && !collabPanel.humanAbsentOnly ? (
          <GroupChatComposer
            session={session}
            isRequesting={isRequesting}
            connectionStatus={connectionStatus}
            mentionConfig={mentionConfig}
            showReconnectToolbar={showReconnectToolbar}
            onSend={handleGroupSend}
            onStop={stop}
            onReconnect={() => {
              void reconnect();
            }}
            draft={draft}
            onDraftChange={setDraft}
            inputRef={inputRef}
            execution={taskExecution}
            quote={messageInteractions.quote}
            onClearQuote={messageInteractions.clearQuote}
            editingMessageId={editingMessageId}
            onCancelEdit={cancelEdit}
          />
        ) : null}
        <ChatLayout.Panel ref={panelRef} onAction={handlePanelAction} bridge={chatBridge} />
      </ChatLayout>
      {session && <FuseSlot group={group} sessionId={session.sessionId} viewerName={activeIdentity?.displayName} />}
    </section>
  );
}
export type { GroupChatPaneProps } from './GroupChatPane.types';
export default GroupChatPane;
