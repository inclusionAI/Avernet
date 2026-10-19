import { Button } from '@/components/ui';
import { MessageSelectionToolbar } from '@/components/Workspace/MessageInteractionToolbar';
import type { GroupView, SessionView } from '@/domain/collaboration';
import { useHistoryPrependAnchor } from '@/pages/Workspace/hooks/useHistoryPrependAnchor';
import type { MessageInteractions } from '@/pages/Workspace/hooks/useMessageInteractions';
import { useStickToBottom } from '@/pages/Workspace/hooks/useStickToBottom';
import { cn } from '@/utils/cn';
import type { ChatMessage } from '@tc-chat/core';
import { BubbleList } from '@tc-chat/ui/es/BubbleList';
import { ArrowDown } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, type MutableRefObject } from 'react';
import { GroupChatBubble, ThinkingBubble } from './GroupChatBubble';
import { GroupChatEmptyState } from './GroupChatEmptyState';

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
  /** 当前视角身份：user=人类视角（主文案引导发送）；bot=Bot 视角（发言由协作面板控制）。 */
  viewerKind?: 'user' | 'bot' | null;
  /** @ 成员面板是否已装配（human 视角）；决定空态点名卡的取舍。 */
  canMention?: boolean;
  /** 空态点名卡落地：写入 '@' 并聚焦（宿主接 inputRef）；不可交互时不传即隐藏行动区。 */
  onInsertDraft?: (text: string) => void;
  /** 历史消息面板定位高亮：滚动到目标行并短暂高亮（3s 由上层 hook 自清）。 */
  highlightMessageId?: string | null;
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
  viewerKind,
  canMention,
  onInsertDraft,
  highlightMessageId,
}: GroupChatMessageListProps) {
  const latestUserMessageId = [...messages].reverse().find((message) => message.role === 'user')?.id;
  const hasStreamingMessage = useMemo(
    () => messages.some((message) => message.role === 'assistant' && message.status === 'streaming'),
    [messages],
  );
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
  // 历史面板定位：滚动到目标消息行（与 chat 页 ChatMessageList 同构的 data-message-id DOM 手法）。
  useEffect(() => {
    if (!highlightMessageId || !listRootRef.current) return;
    const target = listRootRef.current.querySelector(`[data-message-id="${highlightMessageId}"]`);
    if (target instanceof HTMLElement) target.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [highlightMessageId]);
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
        emptyPlaceholder={
          <GroupChatEmptyState
            group={group}
            session={session}
            viewerKind={viewerKind}
            canMention={canMention}
            onInsertDraft={onInsertDraft}
          />
        }
        footer={
          showThinkingBubble ? <ThinkingBubble label={groupBootstrapProcessing ? processingLabel : undefined} /> : null
        }
        renderItem={(message, index) => {
          const isEditable = message.role === 'user' && message.id === latestUserMessageId;
          return (
            // 验收微调（2026-09-14）：底部间距 8→20px——为悬停操作栏（悬挂在消息块下方）让出归属空间，
            // 避免操作栏视觉上贴近下一条消息的发送者行；顶部 8px 不变（密度收敛口径）。
            <div
              data-message-id={message.id}
              className={cn(
                'group relative pt-2 pb-5 transition-colors',
                message.id === highlightMessageId && 'rounded-lg bg-selected',
              )}
            >
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
        // dmore 回底按钮：38×38 白底圆形图标件（artboard-010 ×2 同 path 状态图层，落单枚，
        // 与 chat 页 ChatMessageList 同规格）；unread 计数进角标与 aria-label，markRead 语义零变更。
        <div className="absolute bottom-4 right-6 z-10">
          <Button
            variant="ghost"
            onClick={interactions.markRead}
            aria-label={`回到底部，${interactions.unreadCount} 条新消息`}
            className="size-[38px] rounded-full bg-background p-0 text-muted-foreground shadow-md hover:bg-background"
          >
            <ArrowDown className="h-4 w-4" aria-hidden="true" />
            <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-medium leading-4 text-primary-foreground">
              {interactions.unreadCount}
            </span>
          </Button>
        </div>
      ) : null}
    </div>
  );
}
