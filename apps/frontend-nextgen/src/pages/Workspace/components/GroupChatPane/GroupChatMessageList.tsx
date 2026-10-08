import { Button } from '@/components/ui';
import { MessageSelectionToolbar } from '@/components/Workspace/MessageInteractionToolbar';
import type { GroupView, SessionView } from '@/domain/collaboration';
import { useHistoryPrependAnchor } from '@/pages/Workspace/hooks/useHistoryPrependAnchor';
import type { MessageInteractions } from '@/pages/Workspace/hooks/useMessageInteractions';
import { useStickToBottom } from '@/pages/Workspace/hooks/useStickToBottom';
import type { ChatMessage } from '@tc-chat/core';
import { BubbleList } from '@tc-chat/ui/es/BubbleList';
import { ArrowDown } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from 'react';
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
  userIdentityName?: string | null;
  onQuoteSelected: (text: string) => void;
  onExplainSelected: (text: string) => void;
  onEditMessage: (message: ChatMessage) => void;
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
  userIdentityName,
  onQuoteSelected,
  onExplainSelected,
  onEditMessage,
}: GroupChatMessageListProps) {
  const latestUserMessageId = [...messages].reverse().find((message) => message.role === 'user')?.id;
  const hasStreamingMessage = useMemo(
    () => messages.some((message) => message.role === 'assistant' && message.status === 'streaming'),
    [messages],
  );
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!hasStreamingMessage) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasStreamingMessage]);
  const lastMessage = messages[messages.length - 1];
  const hasSettledAssistantResponse = lastMessage?.role === 'assistant' && lastMessage.status !== 'pending';
  const showThinkingBubble =
    (isRequesting || groupBootstrapProcessing) && !hasStreamingMessage && !hasSettledAssistantResponse;
  const isStreaming = isRequesting || groupBootstrapProcessing;
  // BubbleList 的 isStreaming 贴底 effect 不随流式内容增长重跑（依赖只有 messages.length），
  // 应用层补跟随：用户在底部附近时任何内容增高都持续贴底。
  const listRootRef = useRef<HTMLDivElement | null>(null);
  useStickToBottom(listRootRef);
  const loadMoreWithAnchor = useHistoryPrependAnchor(messages, listRootRef, onLoadMoreHistory);
  const setListRootRef = useCallback(
    (node: HTMLDivElement | null) => {
      listRootRef.current = node;
      (interactions.rootRef as MutableRefObject<HTMLDivElement | null>).current = node;
    },
    [interactions.rootRef],
  );
  const processingLabel =
    group.kind === 'task_master_slave'
      ? 'Manager 正在理解群聊目标…'
      : group.kind === 'free_chat'
      ? 'Driver 正在理解群聊目标…'
      : '正在初始化协作任务…';

  return (
    <div ref={setListRootRef} className="flex min-h-0 flex-1 flex-col bg-background">
      <BubbleList
        messages={messages}
        computeItemKey={(message) => message.id}
        isStreaming={isStreaming}
        followOutput="auto"
        hasMore={hasMoreHistory}
        isLoadingMore={isLoadingMoreHistory}
        className="h-full bg-background px-3 py-3 sm:px-6 sm:py-4"
        onLoadMore={loadMoreWithAnchor}
        emptyPlaceholder="发送一条消息开始协作群对话"
        footer={
          showThinkingBubble ? <ThinkingBubble label={groupBootstrapProcessing ? processingLabel : undefined} /> : null
        }
        renderItem={(message, index) => {
          const isEditable = message.role === 'user' && message.id === latestUserMessageId;
          return (
            // 验收微调（2026-09-14）：底部间距 8→20px——为悬停操作栏（悬挂在消息块下方）让出归属空间，
            // 避免操作栏视觉上贴近下一条消息的发送者行；顶部 8px 不变（密度收敛口径）。
            <div data-message-id={message.id} className="group relative pt-2 pb-5">
              <GroupChatBubble
                message={message}
                isLastMessage={index === messages.length - 1}
                isRequesting={isRequesting}
                group={group}
                participants={session.participants}
                sessionId={session.sessionId}
                userAvatarUrl={userAvatarUrl}
                userIdentityId={userIdentityId}
                userIdentityName={userIdentityName}
                onCopy={(text) => interactions.copyText(text)}
                onEdit={() => onEditMessage(message)}
                isEditable={isEditable}
                now={now}
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
