import { Button } from '@/components/ui';
import { MessageSelectionToolbar } from '@/components/Workspace/MessageInteractionToolbar';
import type { GroupView, SessionView } from '@/domain/collaboration';
import type { MessageInteractions } from '@/pages/Workspace/hooks/useMessageInteractions';
import type { ChatMessage } from '@tc-chat/core';
import { BubbleList } from '@tc-chat/ui/es/BubbleList';
import { ArrowDown } from 'lucide-react';
import { GroupChatBubble, ThinkingBubble } from './GroupChatBubble';

interface GroupChatMessageListProps {
  messages: ChatMessage[];
  group: GroupView;
  session: SessionView;
  isRequesting: boolean;
  groupBootstrapProcessing: boolean;
  hasMoreHistory?: boolean;
  isLoadingMoreHistory?: boolean;
  onLoadMoreHistory?: () => void;
  interactions: MessageInteractions;
  userAvatarUrl?: string;
  userIdentityId?: string | null;
  onQuoteSelected: (text: string) => void;
  onExplainSelected: (text: string) => void;
  onEditMessage: (message: ChatMessage) => void;
  onStop: () => void;
}

/** 群聊消息列表及 Driver/Manager 启动提示。 */
export function GroupChatMessageList({
  messages,
  group,
  session,
  isRequesting,
  groupBootstrapProcessing,
  hasMoreHistory,
  isLoadingMoreHistory,
  onLoadMoreHistory,
  interactions,
  userAvatarUrl,
  userIdentityId,
  onQuoteSelected,
  onExplainSelected,
  onEditMessage,
  onStop,
}: GroupChatMessageListProps) {
  const latestUserMessageId = [...messages].reverse().find((message) => message.role === 'user')?.id;
  const lastMessage = messages[messages.length - 1];
  const showThinkingBubble =
    (isRequesting || groupBootstrapProcessing) &&
    (!lastMessage || lastMessage.role !== 'assistant' || lastMessage.status !== 'streaming');
  const processingLabel =
    group.kind === 'task_master_slave'
      ? 'Manager 正在理解群聊目标…'
      : group.kind === 'free_chat'
      ? 'Driver 正在理解群聊目标…'
      : '正在初始化协作任务…';

  return (
    <div ref={interactions.rootRef} className="flex min-h-0 flex-1 flex-col bg-background">
      <BubbleList
        messages={messages}
        computeItemKey={(message) => message.id}
        isStreaming={isRequesting || groupBootstrapProcessing}
        hasMore={hasMoreHistory}
        isLoadingMore={isLoadingMoreHistory}
        className="h-full bg-background px-3 py-3 sm:px-6 sm:py-4"
        onLoadMore={onLoadMoreHistory}
        emptyPlaceholder="发送一条消息开始协作群对话"
        footer={
          showThinkingBubble ? <ThinkingBubble label={groupBootstrapProcessing ? processingLabel : undefined} /> : null
        }
        renderItem={(message, index) => {
          const isEditable = message.role === 'user' && message.id === latestUserMessageId;
          return (
            <div data-message-id={message.id} className="group relative">
              <GroupChatBubble
                message={message}
                isLastMessage={index === messages.length - 1}
                isRequesting={isRequesting}
                group={group}
                participants={session.participants}
                sessionId={session.sessionId}
                userAvatarUrl={userAvatarUrl}
                userIdentityId={userIdentityId}
                onCopy={(text) => interactions.copyText(text)}
                onEdit={() => onEditMessage(message)}
                isEditable={isEditable}
                onStop={onStop}
              />
            </div>
          );
        }}
      />
      <MessageSelectionToolbar
        selection={interactions.selection}
        onCopy={(text) => interactions.copyText(text, '选中文本')}
        onQuote={onQuoteSelected}
        onExplain={onExplainSelected}
      />
      {interactions.unreadCount > 0 ? (
        <Button
          variant="secondary"
          size="sm"
          className="absolute bottom-4 left-1/2 z-10 -translate-x-1/2 gap-1 rounded-full shadow-md"
          onClick={interactions.markRead}
          aria-label={`回到底部，${interactions.unreadCount} 条新消息`}
        >
          <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
          {interactions.unreadCount} 条新消息
        </Button>
      ) : null}
    </div>
  );
}
