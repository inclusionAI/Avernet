// 通用只读会话面板(origin=others):只渲染历史消息区,不感知具体引擎,
// 也不负责加载(状态由 useConversationHistory 等 Hook 注入)。
// 结构自 BotFriendReadOnlyPanel 迁移而来;旧面板及其 Bot 身份分支已于
// workspace-conversation-navigation-refactor Task 10 删除,本组件是唯一只读入口。
import { Avatar, Button, Empty } from '@/components/ui';
import { ChatMessageList } from '@/components/Workspace/ChatPanel/ChatMessageList';
import { ChatPanelHeader } from '@/components/Workspace/ChatPanel/ChatPanelHeader';
import { getMessageBlocks, getMessageTime } from '@/components/Workspace/ChatPanel/chatPanelPresentation';
import type { ConversationUserView } from '@/domain/conversation/types';
import { useMessageInteractions } from '@/pages/Workspace/hooks/useMessageInteractions';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import type { ChatMessage } from '@tc-chat/core';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { History } from 'lucide-react';

export interface ReadOnlyConversationPanelProps {
  bot: Pick<ChatBotView, 'displayName' | 'avatarUrl'> | null;
  friend: ConversationUserView | null;
  session: BotChatSessionView | null;
  messages: ChatMessage[];
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreError: string | null;
  onRetry: () => void;
  onLoadMore: () => void;
  onOpenSessionList?: () => void;
}

export function ReadOnlyConversationPanel({
  bot,
  friend,
  session,
  messages,
  loading,
  error,
  hasMore,
  isLoadingMore,
  loadMoreError,
  onRetry,
  onLoadMore,
  onOpenSessionList,
}: ReadOnlyConversationPanelProps) {
  const interactions = useMessageInteractions({
    sessionId: session?.sessionId,
    messages,
    isRequesting: false,
  });

  if (!bot || !friend || !session) {
    return (
      <section className="flex min-w-0 flex-1 items-center justify-center bg-background">
        <Empty
          title="请选择一个好友用户会话"
          description="展开好友用户并选择一个会话后，可在这里查看历史消息。"
          icon={<History className="h-5 w-5" />}
        />
      </section>
    );
  }

  if (error) {
    return (
      <section className="flex min-w-0 flex-1 items-center justify-center bg-background">
        <Empty
          title={error}
          description="历史消息暂时无法加载，请稍后重试。"
          icon={<History className="h-5 w-5" />}
          action={
            <Button variant="secondary" size="sm" onClick={onRetry}>
              重试
            </Button>
          }
        />
      </section>
    );
  }

  const target: ConversationTarget = {
    id: session.sessionId,
    name: friend.displayName,
    avatar: friend.displayName.charAt(0) || '人',
    engine: 'OpenClaw',
    status: 'available',
    summary: `与 ${friend.displayName} 的历史对话`,
    kind: 'single',
  };

  const resolveSender = (message: ChatMessage) =>
    message.role === 'assistant'
      ? {
          name: bot.displayName,
          avatar: <Avatar name={bot.displayName} src={bot.avatarUrl} size={32} />,
        }
      : {
          name: friend.displayName,
          avatar: <Avatar name={friend.displayName} size={32} />,
        };

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-background">
      <ChatLayout className="min-h-0 flex-1">
        <ChatPanelHeader target={target} sessionTitle={session.title} onOpenSessionList={onOpenSessionList} />
        {loadMoreError ? (
          <div className="flex items-center justify-center gap-2 border-b border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <span>{loadMoreError}</span>
            <Button variant="ghost" size="sm" className="h-7 text-destructive" onClick={onLoadMore}>
              重试
            </Button>
          </div>
        ) : null}
        <ChatMessageList
          messages={messages}
          isRequesting={false}
          isLoadingMessages={loading}
          interactions={interactions}
          onStop={() => {}}
          onEditMessage={() => {}}
          onQuoteSelected={() => {}}
          onExplainSelected={() => {}}
          resolveSender={resolveSender}
          getMessageTime={getMessageTime}
          getMessageBlocks={getMessageBlocks}
          hasMoreHistory={hasMore}
          isLoadingMoreHistory={isLoadingMore}
          onLoadMoreHistory={onLoadMore}
          readOnly
          emptyPlaceholder="暂无历史消息"
        />
      </ChatLayout>
    </section>
  );
}
