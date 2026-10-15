import { Button } from '@/components/ui';
import type { GroupView, IdentityView, SessionView } from '@/domain/collaboration/types';
import type { DomainResult } from '@/services/workspace/identityService';
import { ListErrorState } from '../ListErrorState';
import type { SessionTab } from './GroupItem.types';
import { GroupSessionRowsSkeleton } from './GroupSidebarSkeletons';
import { SessionItem } from './SessionItem';

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
  onRenameSession?: (sessionId: string, title: string) => Promise<boolean>;
  onDeleteSession?: (sessionId: string) => Promise<boolean>;
  onShareSession?: (sessionId: string, expiresInSeconds?: number) => Promise<DomainResult<{ invitationUrl: string }>>;
  activeIdentity?: IdentityView | null;
}

/** 协作群展开后的会话列表：加载/空态/列表/加载更多/分页错误统一在此自洽渲染。
 *  行自带名称对齐缩进与背景胶囊，容器不渲染树形导轨与缩进底色（对齐对话会话列表）。 */
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
  onRenameSession,
  onDeleteSession,
  onShareSession,
  activeIdentity,
}: GroupSessionsListProps) {
  const loaded = sessions !== undefined;
  const safeSessions = sessions ?? [];
  const visibleSessions =
    sessionTab === 'favorite' ? safeSessions.filter((s) => favoriteSessionIds.includes(s.sessionId)) : safeSessions;
  return (
    <div aria-label={`协作群会话列表：${group.name}`}>
      {error ? (
        <ListErrorState message={error} onRetry={() => void onRetrySessions?.()} />
      ) : !loaded ? (
        /* 加载骨架：回声 SessionItem 行槽及名称起点，对齐对话二级会话行骨架。 */
        <GroupSessionRowsSkeleton rows={3} />
      ) : visibleSessions.length === 0 ? (
        <div className="py-5 pr-4 pl-10">
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
            activeIdentity={activeIdentity}
            onSelectSession={(sessionId) => onSelectSession(group.groupId, sessionId)}
            onToggleFavorite={onToggleFavorite}
            onManageSession={(sessionId) => onManageSession(group.groupId, sessionId)}
            onRenameSession={onRenameSession}
            onDeleteSession={onDeleteSession}
            onShareSession={onShareSession}
          />
        ))
      )}
      {hasMoreSessions && (
        <div className="flex justify-center px-4 pb-2 pt-2">
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
