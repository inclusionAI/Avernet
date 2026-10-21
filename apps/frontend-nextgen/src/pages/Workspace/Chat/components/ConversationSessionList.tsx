import type { ConversationSessionListState } from '@/domain/conversation/types';
import { ListErrorState } from '../../components/ListErrorState';
import type { ConversationSessionListActions } from '../hooks/useConversationSessionActions';
import { ConversationLoadMore } from './ConversationLoadMore';
import { ConversationSessionRow } from './ConversationSessionRow';
import { ConversationSessionRowsSkeleton } from './ConversationSkeletons';

/** 我发起 / 好友 Bot 的交互式 Session 列表(选中 / 加载更多均来自 Hook 模型)。 */
export function ConversationSessionList(props: {
  actions?: ConversationSessionListActions;
  sessions: ConversationSessionListState | undefined;
  selectedSessionId: string | null;
  onSelectSession(sessionId: string): void;
  onLoadMore(): void;
  onToggleFavorite?(sessionId: string): void;
  isFavoritePending(sessionId: string): boolean;
  /** Bot 正在回复中的会话 id(2026-10-21):透传行组件渲染 loading。 */
  replyingSessionId?: string | null;
}) {
  const {
    actions,
    sessions,
    selectedSessionId,
    onSelectSession,
    onLoadMore,
    onToggleFavorite,
    isFavoritePending,
    replyingSessionId,
  } = props;
  // 未加载 ≠ 空列表:无缓存、或首拉在途(loading 且尚无 items)都只给行骨架,
  // 不闪「暂无会话」;已在途刷量(loading 但保留旧 items)继续展示旧行。
  if (!sessions || (sessions.loading && sessions.items.length === 0)) {
    return <ConversationSessionRowsSkeleton rows={3} />;
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
            replyingSessionId={replyingSessionId}
            actions={
              actions
                ? {
                    run: (action) => actions.run(session.sessionId, action),
                    pending:
                      actions.pending ||
                      Boolean(sessions.loading || sessions.isLoadingMore) ||
                      isFavoritePending(session.sessionId),
                  }
                : undefined
            }
          />
        ))}
      </div>
      {sessions.hasMore && (
        <ConversationLoadMore
          loading={sessions.isLoadingMore}
          disabled={
            actions?.pending || sessions.items.some((session) => isFavoritePending(session.sessionId))
          }
          onClick={() => onLoadMore()}
        />
      )}
      {sessions.loadMoreError && <ListErrorState message={sessions.loadMoreError} onRetry={onLoadMore} />}
    </>
  );
}
