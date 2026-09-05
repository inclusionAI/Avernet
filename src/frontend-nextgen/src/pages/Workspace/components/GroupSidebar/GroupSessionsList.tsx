import { Button, Skeleton } from '@/components/ui';
import type { GroupView, SessionView } from '@/domain/collaboration/types';
import { ListErrorState } from '../ListErrorState';
import { SessionItem } from './SessionItem';
import type { SessionTab } from './GroupItem.types';

interface GroupSessionsListProps {
  group: GroupView;
  sessions: SessionView[] | undefined;
  sessionTab: SessionTab;
  favoriteSessionIds: string[];
  selectedSessionId: string | null;
  hasMoreSessions: boolean;
  isLoadingMoreSessions: boolean;
  onLoadMoreSessions: () => Promise<void>;
  error?: string;
  loadMoreError?: string;
  onRetrySessions?: () => Promise<void>;
  onSelectSession: (groupId: string, sessionId: string) => void;
  onToggleFavorite: (sessionId: string) => void;
  onManageSession: (groupId: string, sessionId: string) => void;
}

/** 协作群展开后的会话列表：加载/空态/列表/加载更多/分页错误统一在此自洽渲染。 */
export function GroupSessionsList({
  group,
  sessions,
  sessionTab,
  favoriteSessionIds,
  selectedSessionId,
  hasMoreSessions,
  isLoadingMoreSessions,
  onLoadMoreSessions,
  error,
  loadMoreError,
  onRetrySessions,
  onSelectSession,
  onToggleFavorite,
  onManageSession,
}: GroupSessionsListProps) {
  const loaded = sessions !== undefined;
  const safeSessions = sessions ?? [];
  const visibleSessions =
    sessionTab === 'favorite' ? safeSessions.filter((s) => favoriteSessionIds.includes(s.sessionId)) : safeSessions;
  return (
    <div aria-label={`协作群会话列表：${group.name}`} className="border-t border-border/60 bg-background">
      <div className="overflow-hidden bg-background">
        {error ? (
          <ListErrorState message={error} onRetry={() => void onRetrySessions?.()} />
        ) : !loaded ? (
          <div>
            {[1, 2, 3].map((i) => (
              <Skeleton.Block key={i} className="h-12 w-full rounded-none border-b border-border last:border-b-0" />
            ))}
          </div>
        ) : visibleSessions.length === 0 ? (
          <div className="px-3 py-5">
            <span className="text-xs text-muted-foreground">
              {sessionTab === 'favorite'
                ? hasMoreSessions
                  ? '当前已加载会话中暂无收藏'
                  : '暂无已收藏会话'
                : '当前协作群暂无会话'}
            </span>
          </div>
        ) : (
          visibleSessions.map((session) => (
            <SessionItem
              key={session.sessionId}
              session={session}
              favorite={favoriteSessionIds.includes(session.sessionId)}
              selected={selectedSessionId === session.sessionId}
              onSelectSession={(sessionId) => onSelectSession(group.groupId, sessionId)}
              onToggleFavorite={onToggleFavorite}
              onManageSession={(sessionId) => onManageSession(group.groupId, sessionId)}
            />
          ))
        )}
      </div>
      {hasMoreSessions && (
        <div className="flex justify-center border-t border-border/60 bg-muted/20 px-[18px] pb-2 pt-2">
          <Button
            variant="ghost"
            size="sm"
            disabled={isLoadingMoreSessions}
            onClick={(event) => {
              event.stopPropagation();
              void onLoadMoreSessions();
            }}
            className="h-8 rounded-md border border-input bg-background px-3 text-xs text-foreground hover:bg-accent"
          >
            {isLoadingMoreSessions ? '正在加载…' : '加载更多会话'}
          </Button>
        </div>
      )}
      {loadMoreError && <ListErrorState message={loadMoreError} onRetry={() => void onLoadMoreSessions()} />}
    </div>
  );
}
