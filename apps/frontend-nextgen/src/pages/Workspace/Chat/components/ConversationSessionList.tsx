import { Button, Skeleton } from '@/components/ui';
import type { ConversationSessionListState } from '@/domain/conversation/types';
import { ListErrorState } from '../../components/ListErrorState';
import { ConversationSessionRow } from './ConversationSessionRow';

/** 我发起 / 好友 Bot 的交互式 Session 列表(选中 / 加载更多均来自 Hook 模型)。 */
export function ConversationSessionList(props: {
  sessions: ConversationSessionListState | undefined;
  selectedSessionId: string | null;
  onSelectSession(sessionId: string): void;
  onLoadMore(): void;
  onToggleFavorite?(sessionId: string): void;
  isFavoritePending(sessionId: string): boolean;
}) {
  const { sessions, selectedSessionId, onSelectSession, onLoadMore, onToggleFavorite, isFavoritePending } = props;
  if (!sessions) {
    // 未加载 ≠ 空列表:展开后由 Hook 首拉,先给骨架。
    return (
      <div className="py-1">
        {[1, 2, 3].map((i) => (
          <Skeleton.Block key={i} className="mx-4 h-[44px] rounded-lg" />
        ))}
      </div>
    );
  }
  if (sessions.error) {
    // 列表失败可通过重新展开 / 切换筛选重试(Hook 契约),不提供独立重试入口。
    return <ListErrorState message={sessions.error} />;
  }
  if (sessions.items.length === 0 && !sessions.hasMore) {
    return <div className="px-4 py-3 text-xs text-muted-foreground">暂无会话</div>;
  }
  return (
    <>
      <div>
        {sessions.items.map((session) => (
          <ConversationSessionRow
            key={session.sessionId}
            session={session}
            selected={selectedSessionId === session.sessionId}
            onSelect={() => onSelectSession(session.sessionId)}
            onToggleFavorite={onToggleFavorite ? () => onToggleFavorite(session.sessionId) : undefined}
            favoritePending={isFavoritePending(session.sessionId)}
          />
        ))}
      </div>
      {sessions.hasMore && (
        <div className="flex justify-center px-4 pb-2 pt-1">
          <Button
            variant="ghost"
            size="sm"
            disabled={sessions.isLoadingMore || sessions.items.some((session) => isFavoritePending(session.sessionId))}
            onClick={() => onLoadMore()}
            className="h-7 rounded-md border border-input bg-background px-3 text-xs text-foreground hover:bg-accent"
          >
            {sessions.isLoadingMore ? '正在加载…' : '加载更多会话'}
          </Button>
        </div>
      )}
      {sessions.loadMoreError && <ListErrorState message={sessions.loadMoreError} onRetry={onLoadMore} />}
    </>
  );
}
