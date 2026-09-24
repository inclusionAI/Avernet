import { Empty, Input } from '@/components/ui';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import type { BotFriendSessionView, BotIdentityFriendView } from '@/services/workspace/botFriendConversationService';
import { Search } from 'lucide-react';
import { useState } from 'react';
import { ResizableWorkspaceSidebar } from '../ResizableWorkspaceSidebar';
import { WorkspacePrimaryTabs } from '../WorkspacePrimaryTabs';
import { WorkspaceSidebarCollapsedRail } from '../WorkspaceSidebarCollapsedRail';
import { BotFriendSection } from './BotFriendSection';

export interface BotFriendConversationSidebarProps {
  view: WorkspaceView;
  onViewChange: (view: WorkspaceView) => void;
  availableViews: readonly WorkspaceView[];
  identityName: string;
  humanFriends: BotIdentityFriendView[];
  botFriends: BotIdentityFriendView[];
  humanLoading: boolean;
  botLoading: boolean;
  humanError: string | null;
  botError: string | null;
  onRetryHuman: () => void;
  onRetryBot: () => void;
  expandedFriendUserId: string | null;
  sessions: BotFriendSessionView[];
  sessionsLoading: boolean;
  sessionsError: string | null;
  selectedSessionId: string | null;
  hasMoreSessions: boolean;
  isLoadingMoreSessions: boolean;
  loadMoreSessionsError: string | null;
  onToggleFriend: (friendUserId: string) => void;
  onSelectSession: (sessionId: string) => void;
  onRetrySessions: () => void;
  onLoadMoreSessions: () => void;
}

export function BotFriendConversationList(props: BotFriendConversationSidebarProps) {
  const [search, setSearch] = useState('');
  const keyword = search.trim().toLowerCase();
  const humanFriends = props.humanFriends.filter(
    (friend) =>
      !keyword || friend.displayName.toLowerCase().includes(keyword) || friend.actorId.toLowerCase().includes(keyword),
  );
  const botFriends = props.botFriends.filter(
    (friend) => !keyword || friend.displayName.toLowerCase().includes(keyword),
  );
  const noSearchMatches = Boolean(keyword) && humanFriends.length === 0 && botFriends.length === 0;
  const sectionProps = {
    expandedFriendUserId: props.expandedFriendUserId,
    sessions: props.sessions,
    sessionsLoading: props.sessionsLoading,
    sessionsError: props.sessionsError,
    selectedSessionId: props.selectedSessionId,
    hasMoreSessions: props.hasMoreSessions,
    isLoadingMoreSessions: props.isLoadingMoreSessions,
    loadMoreSessionsError: props.loadMoreSessionsError,
    onToggleFriend: props.onToggleFriend,
    onSelectSession: props.onSelectSession,
    onRetrySessions: props.onRetrySessions,
    onLoadMoreSessions: props.onLoadMoreSessions,
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto bg-muted/20">
        <div className="sticky top-0 z-20 border-b border-border/70 bg-muted/20 pt-1 backdrop-blur-sm">
          <div className="flex h-10 items-center gap-2 px-4">
            <WorkspacePrimaryTabs
              value={props.view}
              options={[...props.availableViews]}
              onChange={props.onViewChange}
            />
          </div>
          <div className="my-2 px-4">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                className="h-9 pl-9"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索好友名称"
                aria-label="搜索好友"
              />
            </div>
          </div>
        </div>
        {noSearchMatches ? (
          <Empty compact title="未找到匹配的好友" description="请尝试其他名称。" />
        ) : (
          <div>
            <BotFriendSection
              title={`${props.identityName}的好友用户`}
              targetType="human"
              friends={humanFriends}
              loading={props.humanLoading}
              error={props.humanError}
              onRetry={props.onRetryHuman}
              {...sectionProps}
            />
            <BotFriendSection
              title={`${props.identityName}的好友 Bot`}
              targetType="bot"
              friends={botFriends}
              loading={props.botLoading}
              error={props.botError}
              onRetry={props.onRetryBot}
              {...sectionProps}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export function BotFriendConversationSidebar(props: BotFriendConversationSidebarProps) {
  return (
    <ResizableWorkspaceSidebar
      ariaLabel="Bot 好友会话侧栏"
      collapsedContent={
        <WorkspaceSidebarCollapsedRail
          value={props.view}
          options={[...props.availableViews]}
          onChange={props.onViewChange}
        />
      }
    >
      <BotFriendConversationList {...props} />
    </ResizableWorkspaceSidebar>
  );
}
