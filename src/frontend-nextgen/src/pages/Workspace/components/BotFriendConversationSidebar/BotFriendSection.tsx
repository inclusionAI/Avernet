import { Button, Skeleton } from '@/components/ui';
import type {
  BotFriendActorType,
  BotFriendSessionView,
  BotIdentityFriendView,
} from '@/services/workspace/botFriendConversationService';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { ListErrorState } from '../ListErrorState';
import { BotFriendRow } from './BotFriendRow';
import { BotFriendSessionRow } from './BotFriendSessionRow';

interface BotFriendSectionProps {
  title: string;
  targetType: BotFriendActorType;
  friends: BotIdentityFriendView[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
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

export function BotFriendSection({
  title,
  targetType,
  friends,
  loading,
  error,
  onRetry,
  expandedFriendUserId,
  sessions,
  sessionsLoading,
  sessionsError,
  selectedSessionId,
  hasMoreSessions,
  isLoadingMoreSessions,
  loadMoreSessionsError,
  onToggleFriend,
  onSelectSession,
  onRetrySessions,
  onLoadMoreSessions,
}: BotFriendSectionProps) {
  const [collapsed, setCollapsed] = useState(false);
  const sectionId = `bot-friend-section-${targetType}`;

  return (
    <section aria-label={title}>
      <div className="flex min-h-9 items-center">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setCollapsed((current) => !current)}
          aria-expanded={!collapsed}
          aria-label={`${title} (${friends.length})`}
          aria-controls={sectionId}
          className="flex h-auto min-h-9 min-w-0 flex-1 items-center gap-1 rounded-none border-0 bg-transparent px-4 py-2 text-xs font-medium text-foreground hover:bg-accent/50"
        >
          {collapsed ? (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          )}
          <span className="min-w-0 flex-1 truncate text-left">
            {title} ({friends.length})
          </span>
        </Button>
      </div>
      {!collapsed ? (
        <div id={sectionId}>
          {error ? (
            <ListErrorState message={error} onRetry={onRetry} />
          ) : loading ? (
            <div className="overflow-hidden">
              {[1, 2].map((item) => (
                <Skeleton.Block key={item} className="h-16 w-full rounded-none" />
              ))}
            </div>
          ) : friends.length === 0 ? (
            <div className="flex min-h-[80px] items-center justify-center py-4 text-xs text-muted-foreground">
              {targetType === 'human' ? '暂无好友用户' : '暂无好友 Bot'}
            </div>
          ) : (
            <div>
              {friends.map((friend) => {
                const expanded = targetType === 'human' && expandedFriendUserId === friend.actorId;
                return (
                  <BotFriendRow
                    key={`${targetType}:${friend.actorId}`}
                    friend={friend}
                    expanded={expanded}
                    onToggle={onToggleFriend}
                  >
                    <div aria-label={`好友用户会话列表：${friend.displayName}`} className="pl-6">
                      {sessionsError ? (
                        <ListErrorState message={sessionsError} onRetry={onRetrySessions} />
                      ) : sessionsLoading ? (
                        <div>
                          {[1, 2].map((item) => (
                            <Skeleton.Block key={item} className="h-12 w-full rounded-none" />
                          ))}
                        </div>
                      ) : sessions.length === 0 ? (
                        <div className="flex min-h-14 items-center px-3 text-xs text-muted-foreground">暂无会话</div>
                      ) : (
                        sessions.map((session) => (
                          <BotFriendSessionRow
                            key={session.sessionId}
                            session={session}
                            selected={selectedSessionId === session.sessionId}
                            onSelect={onSelectSession}
                          />
                        ))
                      )}
                      {hasMoreSessions ? (
                        <div className="flex justify-center px-4 py-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={isLoadingMoreSessions}
                            onClick={onLoadMoreSessions}
                            className="h-8 border border-input bg-background px-3 text-xs"
                          >
                            {isLoadingMoreSessions ? '正在加载…' : '加载更多会话'}
                          </Button>
                        </div>
                      ) : null}
                      {loadMoreSessionsError ? (
                        <ListErrorState message={loadMoreSessionsError} onRetry={onLoadMoreSessions} />
                      ) : null}
                    </div>
                  </BotFriendRow>
                );
              })}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
