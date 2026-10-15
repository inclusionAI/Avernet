// GroupChatPane 消息区视图分支（无会话空态 / 加载骨架 / 错误重试 / 消息列表）：
// 从主组件抽离的下沉组件，使主文件保持在工程红线（Component 300 行）之内。
// 分支形态与主组件内联实现一一对应，无行为变化。
import { Button, Empty } from '@/components/ui';
import { ChatMessageSkeleton } from '@/components/Workspace/ChatPanel/ChatMessageSkeleton';
import type { GroupView } from '@/domain/collaboration';
import { filterVisibleGroupMessages } from '@/pages/Workspace/hooks/groupChatHistoryUtils';
import { useMessageInteractions } from '@/pages/Workspace/hooks/useMessageInteractions';
import { useMessageEdit } from '@/pages/Workspace/hooks/useMessageEdit';
import { RefreshCw, Sparkles } from 'lucide-react';
import type { GroupChatPaneProps } from './GroupChatPane.types';
import { GroupChatMessageList } from './GroupChatMessageList';

interface GroupChatMessageAreaProps {
  session: GroupChatPaneProps['session'];
  /** 主组件在 !group 早退后才会渲染本组件，故这里收非空群。 */
  group: GroupView;
  /** 空消息一律骨架屏（两态强制），骨架期间不渲染错误态/空态。 */
  isLoadingHistory: boolean;
  groupBootstrapProcessing: GroupChatPaneProps['groupBootstrapProcessing'];
  showError: boolean;
  supportError: GroupChatPaneProps['supportState']['error'];
  reloadHistory: GroupChatPaneProps['reloadHistory'];
  messages: ReturnType<typeof filterVisibleGroupMessages>;
  /** Bot 端是否仍有待回消息（isRequesting && isWaitingForBot，主组件派生后传入）。 */
  isBotResponding: boolean;
  hasMoreHistory: GroupChatPaneProps['hasMoreHistory'];
  isLoadingMoreHistory: GroupChatPaneProps['isLoadingMoreHistory'];
  onLoadMoreHistory: GroupChatPaneProps['onLoadMoreHistory'];
  highlightMessageId: GroupChatPaneProps['highlightMessageId'];
  interactions: ReturnType<typeof useMessageInteractions>;
  userAvatarUrl: GroupChatPaneProps['userAvatarUrl'];
  userIdentityId: GroupChatPaneProps['userIdentityId'];
  userIdentityName: GroupChatPaneProps['userIdentityName'];
  viewerKind: 'user' | 'bot' | null;
  canMention: boolean;
  onQuoteSelected: (text: string) => void;
  onExplainSelected: (text: string) => void;
  onEditMessage: ReturnType<typeof useMessageEdit>['editMessage'];
  /** 空态行动区落地动作（composer 可见时才提供；不可发送视角不渲染点名卡）。 */
  onInsertDraft?: (text: string) => void;
}

export function GroupChatMessageArea({
  session,
  group,
  isLoadingHistory,
  groupBootstrapProcessing = false,
  showError,
  supportError,
  reloadHistory,
  messages,
  isBotResponding,
  hasMoreHistory,
  isLoadingMoreHistory,
  onLoadMoreHistory,
  highlightMessageId,
  interactions,
  userAvatarUrl,
  userIdentityId,
  userIdentityName,
  viewerKind,
  canMention,
  onQuoteSelected,
  onExplainSelected,
  onEditMessage,
  onInsertDraft,
}: GroupChatMessageAreaProps) {
  return !session ? (
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
        <ChatMessageSkeleton ariaLabel="加载协作群会话历史" paddingClassName="px-3 py-3 sm:px-6 sm:py-4" />
      ) : showError ? (
        <div className="flex min-h-0 flex-1 items-center justify-center px-3 py-3 sm:px-6 sm:py-4">
          <Empty
            title="加载会话历史失败"
            description={supportError ?? '协作群连接出现异常，请重试。'}
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
          isRequesting={isBotResponding}
          groupBootstrapProcessing={groupBootstrapProcessing}
          hasMoreHistory={hasMoreHistory}
          isLoadingMoreHistory={isLoadingMoreHistory}
          onLoadMoreHistory={onLoadMoreHistory}
          highlightMessageId={highlightMessageId}
          interactions={interactions}
          userAvatarUrl={userAvatarUrl}
          userIdentityId={userIdentityId}
          userIdentityName={userIdentityName}
          onQuoteSelected={onQuoteSelected}
          onExplainSelected={onExplainSelected}
          onEditMessage={onEditMessage}
          viewerKind={viewerKind}
          canMention={canMention}
          onInsertDraft={onInsertDraft}
        />
      )}
    </div>
  );
}