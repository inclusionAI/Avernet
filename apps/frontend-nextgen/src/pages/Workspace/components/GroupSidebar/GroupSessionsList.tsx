import { Button, Skeleton } from '@/components/ui';
import type { GroupView, IdentityView, SessionView } from '@/domain/collaboration/types';
import type { DomainResult } from '@/services/workspace/identityService';
import { ListErrorState } from '../ListErrorState';
import type { SessionTab } from './GroupItem.types';
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
  onShareSession?: (sessionId: string) => Promise<DomainResult<{ invitationUrl: string }>>;
  activeIdentity?: IdentityView | null;
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
    /* v1.4：展开会话区容器整体缩进 + 树形连接线（1px 竖线贴会话区
       左缘起点，颜色取 border 全值）——贴边避免线两侧空白造成的割裂感。
       验收微调：去掉缩进区极浅底色（与 Bot 侧栏一致，消除条带感）。 */
    /* 验收微调：树形干线改由每行 SessionCard 自带（含末行截断），容器不再渲染贯穿线。 */
    <div aria-label={`协作群会话列表：${group.name}`} className="pl-6">
      {/* 验收微调：去掉 overflow-hidden——行内拐角导轨（-left-6）需向缩进区延伸，不能被裁剪。 */}
      <div>
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
      </div>
      {hasMoreSessions && (
        /* 验收微调：加载更多行去背景与分割线，仅保留整体留白。 */
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
