import { Button } from '@/components/ui';
import { ChatMessageSkeleton } from '@/components/Workspace/ChatPanel/ChatMessageSkeleton';
import {
  MessageCopyAction,
  MessageInteractionToolbar,
  MessageSelectionToolbar,
} from '@/components/Workspace/MessageInteractionToolbar';
import { getMessageSpacingClass } from '@/components/Workspace/messagePresentation';
import { MessageSenderLayout, MessageSenderMeta } from '@/components/Workspace/MessageSenderMeta';
import { useHistoryPrependAnchor } from '@/pages/Workspace/hooks/useHistoryPrependAnchor';
import type { MessageInteractions } from '@/pages/Workspace/hooks/useMessageInteractions';
import { getLatestUserMessageId, getMessageText } from '@/pages/Workspace/hooks/useMessageInteractions';
import { useStickToBottom } from '@/pages/Workspace/hooks/useStickToBottom';
import type { Block, ChatMessage } from '@tc-chat/core';
import { Bubble } from '@tc-chat/ui/es/Bubble';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { aixUiPlugin, fileRefPlugin } from '@tc-chat/ui/es/MarkdownRender';
import { SystemNotice } from '@tc-chat/ui/es/SystemNotice';
import { ArrowDown } from 'lucide-react';
import { useCallback, useEffect, useRef, type MutableRefObject, type ReactNode } from 'react';

interface ChatMessageListProps {
  messages: ChatMessage[];
  isRequesting: boolean;
  isLoadingMessages: boolean;
  interactions: MessageInteractions;
  onStop: () => void;
  onEditMessage: (message: ChatMessage) => void;
  onQuoteSelected: (text: string) => void;
  onExplainSelected: (text: string) => void;
  resolveSender: (message: ChatMessage) => { name: string; avatar: ReactNode };
  getMessageTime: (message: ChatMessage) => string | undefined;
  getMessageBlocks: (message: ChatMessage) => Block[];
  hasMoreHistory?: boolean;
  isLoadingMoreHistory?: boolean;
  onLoadMoreHistory?: () => void;
  readOnly?: boolean;
  emptyPlaceholder?: string;
  /** 待定位高亮的消息 id（历史面板结果点击）：滚动至该消息并短暂高亮（chat-header-panels）。 */
  highlightMessageId?: string | null;
}

export function ChatMessageList({
  messages,
  isRequesting,
  isLoadingMessages,
  interactions,
  onStop,
  onEditMessage,
  onQuoteSelected,
  onExplainSelected,
  resolveSender,
  getMessageTime,
  getMessageBlocks,
  hasMoreHistory,
  isLoadingMoreHistory,
  onLoadMoreHistory,
  readOnly = false,
  emptyPlaceholder,
  highlightMessageId,
}: ChatMessageListProps) {
  const latestUserMessageId = getLatestUserMessageId(messages);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  // BubbleList（ChatLayout.List 内核）的 isStreaming 贴底 effect 不随流式内容增长重跑
  //（依赖只有 messages.length），流式结束后的最终渲染/图片加载/输入框增高也不跟随；
  // 应用层补跟随：用户在底部附近时任何内容增高都持续贴底。
  const listRootRef = useRef<HTMLDivElement | null>(null);
  useStickToBottom(listRootRef);
  const setListRootRef = useCallback(
    (node: HTMLDivElement | null) => {
      listRootRef.current = node;
      (interactions.rootRef as MutableRefObject<HTMLDivElement | null>).current = node;
    },
    [interactions.rootRef],
  );

  const loadMoreWithAnchor = useHistoryPrependAnchor(messages, listRootRef, onLoadMoreHistory);

  // 定位高亮（历史消息面板结果点击）：滚动至目标消息行并呈现高亮态；id 清空或消息不在已载入范围则不动作。
  useEffect(() => {
    if (!highlightMessageId || !listRootRef.current) return;
    const target = listRootRef.current.querySelector(`[data-message-id="${highlightMessageId}"]`);
    if (target instanceof HTMLElement) target.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [highlightMessageId]);

  const getCurrentMessageText = (messageId: string, fallbackText: string) => {
    const currentMessage = messagesRef.current.find((message) => message.id === messageId);
    return currentMessage ? getMessageText(currentMessage) : fallbackText;
  };

  return (
    <div
      ref={setListRootRef}
      data-workspace-message-list="single-chat"
      className="flex min-h-0 flex-1 flex-col bg-background"
    >
      {isLoadingMessages ? (
        <ChatMessageSkeleton />
      ) : (
        <div data-workspace-message-scroll-region="single-chat" className="flex min-h-0 flex-1 flex-col">
          <ChatLayout.List
            className="h-full bg-background px-3 py-3 sm:py-4 sm:pl-14 sm:pr-3"
            messages={messages}
            computeItemKey={(message) => message.id}
            isStreaming={isRequesting}
            followOutput="auto"
            hasMore={hasMoreHistory}
            isLoadingMore={isLoadingMoreHistory}
            onLoadMore={loadMoreWithAnchor}
            emptyPlaceholder={emptyPlaceholder ?? (readOnly ? '暂无历史消息' : '发送一条消息开始对话')}
            renderItem={(message, index) => {
              if (message.role === 'system') {
                // dmore 系统消息：居中弱化纯文字条（12px 弱灰），无 severity 图标。
                return (
                  <SystemNotice showIcon={false} className="text-content-soft">
                    {message.content}
                  </SystemNotice>
                );
              }
              const isLastMessage = index === messages.length - 1;
              const sender = resolveSender(message);
              const messageText = getMessageText(message);
              return (
                // 分组间距(mb-4/mb-6)落在消息项外壳：操作行随内容列渲染在气泡紧下方，
                // 不再隔着 mb 间距悬浮（dmore 实测：操作图标 x=36 与正文左对齐、距正文 ~8px）。
                <div
                  data-message-id={message.id}
                  className={`group relative rounded-lg transition-colors ${
                    message.id === highlightMessageId ? 'bg-selected' : ''
                  } ${getMessageSpacingClass(messages, index)}`}
                >
                  <MessageSenderLayout
                    avatar={sender.avatar}
                    align={message.role === 'user' ? 'right' : 'left'}
                    meta={
                      <MessageSenderMeta
                        name={sender.name}
                        time={getMessageTime(message)}
                        align={message.role === 'user' ? 'right' : 'left'}
                      />
                    }
                  >
                    <Bubble
                      className="message-bubble-compact [--aix-markdown-font-size:13px] [--aix-font-size-base:13px]"
                      sender={{
                        role: message.role,
                        align: message.role === 'user' ? 'right' : 'left',
                        name: undefined,
                        bubbleColor: message.role === 'user' ? 'hsl(var(--muted))' : undefined,
                        maxWidth: '48rem',
                      }}
                      timestamp={undefined}
                      blocks={getMessageBlocks(message)}
                      preset="openclaw"
                      markdown={{ preset: 'full', extensions: [aixUiPlugin, fileRefPlugin] }}
                      tool={{ defaultCollapsed: !(isLastMessage && isRequesting) }}
                      isStreaming={isLastMessage && isRequesting && message.role === 'assistant'}
                      actions={
                        message.role === 'assistant' ? (
                          <MessageInteractionToolbar
                            onEdit={readOnly ? undefined : () => onEditMessage(message)}
                            isEditable={false}
                            showCopy={false}
                            onCopy={() => interactions.copyText(getCurrentMessageText(message.id, messageText))}
                            isStreaming={!readOnly && isLastMessage && isRequesting}
                            onStop={readOnly ? undefined : onStop}
                          />
                        ) : undefined
                      }
                    />
                    <MessageCopyAction
                      testId={`message-copy-action-${message.id}`}
                      align={message.role === 'user' ? 'right' : 'left'}
                      withinContentColumn
                      onCopy={() => interactions.copyText(getCurrentMessageText(message.id, messageText))}
                      onEdit={readOnly ? undefined : () => onEditMessage(message)}
                      isEditable={
                        !readOnly &&
                        message.role === 'user' &&
                        message.id === latestUserMessageId &&
                        Boolean(messageText.trim())
                      }
                    />
                  </MessageSenderLayout>
                </div>
              );
            }}
          />
        </div>
      )}
      <MessageSelectionToolbar
        selection={interactions.selection}
        onCopy={(text) => interactions.copyText(text, '选中文本')}
        onQuote={readOnly ? undefined : onQuoteSelected}
        onExplain={readOnly ? undefined : onExplainSelected}
      />
      {interactions.unreadCount > 0 ? (
        // dmore 回底按钮：38×38 白底圆形图标件（artboard-005/006 Container w38 r-full bg-white），
        // 右下角浮动；unread 计数以徽标角标保留既有 markRead 语义，行为零变更。
        <div className="absolute bottom-4 right-6 z-10">
          <Button
            variant="ghost"
            onClick={interactions.markRead}
            aria-label={`回到底部，${interactions.unreadCount} 条新消息`}
            className="relative size-[38px] rounded-full bg-background p-0 text-muted-foreground shadow-md hover:bg-background"
          >
            <ArrowDown className="h-4 w-4" aria-hidden="true" />
            {interactions.unreadCount > 0 ? (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-medium leading-4 text-primary-foreground">
                {interactions.unreadCount}
              </span>
            ) : null}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
