import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import { useWorkspace } from '@/hooks/useWorkspace';
import { BotFriendConversationSidebarSlot } from './components/BotFriendConversationSidebarSlot';
import { BotFriendReadOnlyPanel } from './components/BotFriendReadOnlyPanel';

interface BotFriendWorkspaceAreaProps {
  workspace: ReturnType<typeof useWorkspace>;
  view: WorkspaceView;
  onViewChange: (view: WorkspaceView) => void;
  availableViews: WorkspaceView[];
  mobileListOpen: boolean;
  onMobileListClose: () => void;
  onOpenMobileList: () => void;
}

/** Bot 工作身份的只读好友会话区域；与 Human→Bot 交互式单聊保持物理分支。 */
export function BotFriendWorkspaceArea({
  workspace,
  view,
  onViewChange,
  availableViews,
  mobileListOpen,
  onMobileListClose,
  onOpenMobileList,
}: BotFriendWorkspaceAreaProps) {
  const model = workspace.botFriendConversation;
  const sessions = model.sessions;
  const history = model.history;
  return (
    <>
      <BotFriendConversationSidebarSlot
        sidebarProps={{
          view,
          onViewChange,
          availableViews,
          identityName: workspace.activeIdentity?.displayName ?? '当前 Bot',
          humanFriends: model.humanFriends,
          botFriends: model.botFriends,
          humanLoading: model.humanLoading,
          botLoading: model.botLoading,
          humanError: model.humanError,
          botError: model.botError,
          onRetryHuman: model.reloadHuman,
          onRetryBot: model.reloadBot,
          expandedFriendUserId: sessions.expandedFriendUserId,
          sessions: sessions.sessions,
          sessionsLoading: sessions.loading,
          sessionsError: sessions.error,
          selectedSessionId: sessions.selectedSession?.sessionId ?? null,
          hasMoreSessions: sessions.hasMore,
          isLoadingMoreSessions: sessions.isLoadingMore,
          loadMoreSessionsError: sessions.loadMoreError,
          onToggleFriend: sessions.toggleFriend,
          onSelectSession: sessions.selectSession,
          onRetrySessions: sessions.retry,
          onLoadMoreSessions: () => void sessions.loadMore(),
        }}
        mobileListOpen={mobileListOpen}
        onMobileListClose={onMobileListClose}
      />
      <BotFriendReadOnlyPanel
        botIdentity={workspace.activeIdentity}
        friend={model.selectedFriend}
        session={sessions.selectedSession}
        messages={history.messages}
        loading={history.loading}
        error={history.error}
        hasMore={history.hasMore}
        isLoadingMore={history.isLoadingMore}
        loadMoreError={history.loadMoreError}
        onRetry={history.retry}
        onLoadMore={() => void history.loadMore()}
        onOpenSessionList={onOpenMobileList}
      />
    </>
  );
}
