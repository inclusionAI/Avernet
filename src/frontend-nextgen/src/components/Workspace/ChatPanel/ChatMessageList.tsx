import { Button, Spin } from '@/components/ui';
import {
  MessageCopyAction,
  MessageInteractionToolbar,
  MessageSelectionToolbar,
} from '@/components/Workspace/MessageInteractionToolbar';
import { getMessageSpacingClass } from '@/components/Workspace/messagePresentation';
import { MessageSenderLayout, MessageSenderMeta } from '@/components/Workspace/MessageSenderMeta';
import type { MessageInteractions } from '@/pages/Workspace/hooks/useMessageInteractions';
import { getLatestUserMessageId, getMessageText } from '@/pages/Workspace/hooks/useMessageInteractions';
import type { Block, ChatMessage } from '@tc-chat/core';
import { Bubble } from '@tc-chat/ui/es/Bubble';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { aixUiPlugin, fileRefPlugin } from '@tc-chat/ui/es/MarkdownRender';
import { SystemNotice } from '@tc-chat/ui/es/SystemNotice';
import { ArrowDown } from 'lucide-react';
import { useRef, type ReactNode } from 'react';

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
}: ChatMessageListProps) {
  const latestUserMessageId = getLatestUserMessageId(messages);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  const getCurrentMessageText = (messageId: string, fallbackText: string) => {
    const currentMessage = messagesRef.current.find((message) => message.id === messageId);
    return currentMessage ? getMessageText(currentMessage) : fallbackText;
  };

  return (
    <div
      ref={interactions.rootRef}
      data-workspace-message-list="single-chat"
      className="flex min-h-0 flex-1 flex-col bg-background"
    >
      {isLoadingMessages ? (
        <div className="flex min-h-0 flex-1 items-center justify-center" aria-label="加载会话消息">
          <Spin />
        </div>
      ) : (
        <div data-workspace-message-scroll-region="single-chat" className="flex min-h-0 flex-1 flex-col">
          <ChatLayout.List
            className="h-full bg-background px-3 py-3 sm:px-6 sm:py-4"
            messages={messages}
            computeItemKey={(message) => message.id}
            isStreaming={isRequesting}
            emptyPlaceholder="发送一条消息开始对话"
            renderItem={(message, index) => {
              if (message.role === 'system') {
                return <SystemNotice>{message.content}</SystemNotice>;
              }
              const isLastMessage = index === messages.length - 1;
              const sender = resolveSender(message);
              const messageText = getMessageText(message);
              return (
                <div data-message-id={message.id} className="group relative">
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
                      className={`${getMessageSpacingClass(
                        messages,
                        index,
                      )} message-bubble-compact [--aix-markdown-font-size:14px] [--aix-font-size-base:14px]`}
                      sender={{
                        role: message.role,
                        align: message.role === 'user' ? 'right' : 'left',
                        name: undefined,
                        bubbleColor: message.role === 'user' ? 'hsl(var(--primary) / 0.1)' : undefined,
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
                            onEdit={() => onEditMessage(message)}
                            isEditable={false}
                            showCopy={false}
                            onCopy={() => interactions.copyText(getCurrentMessageText(message.id, messageText))}
                            isStreaming={isLastMessage && isRequesting}
                            onStop={onStop}
                          />
                        ) : undefined
                      }
                    />
                  </MessageSenderLayout>
                  <MessageCopyAction
                    testId={`message-copy-action-${message.id}`}
                    align={message.role === 'user' ? 'right' : 'left'}
                    onCopy={() => interactions.copyText(getCurrentMessageText(message.id, messageText))}
                    onEdit={() => onEditMessage(message)}
                    isEditable={
                      message.role === 'user' && message.id === latestUserMessageId && Boolean(messageText.trim())
                    }
                  />
                </div>
              );
            }}
          />
        </div>
      )}
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
