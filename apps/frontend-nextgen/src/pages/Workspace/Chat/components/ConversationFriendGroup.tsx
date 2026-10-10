// origin=others 视角下的好友用户分组:分组行 + 只读 Session 列表。
// 状态机来自 Hook 模型缓存(unloaded/loading/loaded/error):
// - unloaded 不判空、不显示"暂无会话"(未加载 ≠ 空列表);
// - 仅当该好友的 Session 请求成功且为空时整组隐藏;
// - error 保留分组并给局部重试;分页失败保留已加载行。
import { Button } from '@/components/ui';
import type { ConversationFriendGroupView } from '@/domain/conversation/types';
import { cn } from '@/utils/cn';
import { ChevronDown, ChevronRight } from 'lucide-react';
import React from 'react';
import { AvatarTile } from '../../components/AvatarTile';
import { ListErrorState } from '../../components/ListErrorState';
import { ConversationLoadMore } from './ConversationLoadMore';
import { ConversationSessionRow } from './ConversationSessionRow';
import { ConversationSessionRowsSkeleton } from './ConversationSkeletons';

export interface ConversationFriendGroupProps {
  group: ConversationFriendGroupView;
  /** 展开态以 Store(expandedFriendUserIdsByBotId)为准,不读 group.expanded。 */
  expanded: boolean;
  /** 跨好友分组的当前选中只读 Session。 */
  selectedSessionId: string | null;
  onToggle(): void;
  onSelectSession(sessionId: string): void;
  onRetry(): void;
  onLoadMore(): void;
}

export const ConversationFriendGroup = React.memo(function ConversationFriendGroup({
  group,
  expanded,
  selectedSessionId,
  onToggle,
  onSelectSession,
  onRetry,
  onLoadMore,
}: ConversationFriendGroupProps) {
  const { sessions } = group;
  // 唯一的隐藏条件:Session 请求成功但列表为空(成功空列表才隐藏分组)。
  if (group.state === 'loaded' && sessions.items.length === 0 && !sessions.error) {
    return null;
  }

  return (
    <div className="pl-6">
      <div
        className={cn(
          'group relative flex min-h-12 items-center gap-2 px-4 py-1.5 transition-colors',
          expanded ? 'bg-muted' : 'hover:bg-accent/50',
        )}
      >
        <Button
          variant="ghost"
          aria-label={group.friend.displayName}
          aria-expanded={expanded}
          onClick={onToggle}
          className="flex h-auto min-w-0 flex-1 items-center justify-start gap-3 rounded-none px-0 py-1 text-left hover:bg-transparent"
        >
          <AvatarTile
            label={group.friend.displayName}
            className={expanded ? 'bg-primary/15 text-primary ring-1 ring-primary/30' : undefined}
            fallbackContent={<span className="text-[10px] font-semibold">👤</span>}
          />
          <span className="min-w-0 flex-1 truncate text-sm text-foreground">{group.friend.displayName}</span>
        </Button>
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
      </div>

      {expanded && (
        <div aria-label={`${group.friend.displayName}的会话列表`}>
          {group.state === 'loading' || sessions.loading ? (
            <ConversationSessionRowsSkeleton rows={2} />
          ) : group.state === 'error' ? (
            <ListErrorState message={group.error ?? '会话加载失败'} onRetry={onRetry} />
          ) : group.state === 'loaded' && sessions.error ? (
            <ListErrorState message={sessions.error} onRetry={onRetry} />
          ) : group.state === 'loaded' ? (
            <>
              <div>
                {sessions.items.map((session) => (
                  <ConversationSessionRow
                    key={session.sessionId}
                    session={session}
                    readOnly
                    selected={selectedSessionId === session.sessionId}
                    onSelect={() => onSelectSession(session.sessionId)}
                  />
                ))}
              </div>
              {sessions.hasMore && (
                <ConversationLoadMore loading={sessions.isLoadingMore} onClick={() => onLoadMore()} />
              )}
              {sessions.loadMoreError && <ListErrorState message={sessions.loadMoreError} onRetry={onLoadMore} />}
            </>
          ) : null}
        </div>
      )}
    </div>
  );
});
