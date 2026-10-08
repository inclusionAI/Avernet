import { CreateGroupSessionModal } from '@/components/CollaborationSquare/CreateGroupSessionModal';
import GroupCard from '@/components/CollaborationSquare/GroupCard';
import { GroupMembersModal } from '@/components/CollaborationSquare/GroupMembersModal';
import SquareSearchBar from '@/components/CollaborationSquare/SquareSearchBar';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { RefreshCw } from 'lucide-react';
import { useEffect, useRef } from 'react';

const LOAD_MORE_PRELOAD_DISTANCE = 420;

function GroupLoadingState() {
  return (
    <div aria-label="正在加载公开协作群" className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {[0, 1, 2].map((item) => (
        <Card key={item}>
          <Skeleton.Card />
        </Card>
      ))}
    </div>
  );
}

export interface PublicGroupSquareSectionProps {
  /** 协作广场群视图（由 Shell 从 `useCollaborationSquare('group')` 组装后注入）。 */
  square: ReturnType<typeof useCollaborationSquare>;
  /** 滚动容器 ref，用于群列表 IntersectionObserver 的 root（Shell 共享的 `<main>`）。 */
  scrollRootRef: React.RefObject<HTMLElement>;
  /** 是否还可加载下一页（Shell 计算后透传，保持与全局 `onScroll` 预取一致）。 */
  canLoadMore: boolean;
}

/**
 * 公开协作群广场区块：搜索栏 + loading/empty/error 态 + 群卡网格 + loadMore 哨兵 + 成员弹层 + 创建会话弹层。
 * 从 `SquarePageShell` 等价抽出，行为与视觉与原内联实现完全一致（Shell 二元 → 三分发重构时下沉）。
 */
export function PublicGroupSquareSection({ square, scrollRootRef, canLoadMore }: PublicGroupSquareSectionProps) {
  const sentinelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = scrollRootRef.current;
    const sentinel = sentinelRef.current;
    if (!root || !sentinel || !canLoadMore || typeof IntersectionObserver === 'undefined') {
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) void square.loadMore();
      },
      { root, rootMargin: `0px 0px ${LOAD_MORE_PRELOAD_DISTANCE}px 0px`, threshold: 0 },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [canLoadMore, scrollRootRef, square.loadMore]);

  return (
    <>
      <SquareSearchBar
        resource="group"
        query={square.groupQuery}
        onQueryChange={(query) => square.setQuery('group', query)}
      />
      {square.loading && <GroupLoadingState />}
      {!square.loading && square.error && (
        <Card>
          <Empty
            title="协作广场加载失败"
            description={square.error}
            action={
              <Button onClick={() => void square.load()} leftIcon={<RefreshCw aria-hidden className="h-4 w-4" />}>
                重新加载
              </Button>
            }
          />
        </Card>
      )}
      {!square.loading && !square.error && square.visibleGroups.length === 0 && (
        <Card>
          <Empty title="没有找到公开协作群" description="尝试更换群名称或清除搜索。" />
        </Card>
      )}
      {!square.loading && !square.error && square.visibleGroups.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {square.visibleGroups.map((group) => (
            <GroupCard
              key={group.id}
              group={group}
              busy={square.busyKeys.includes(`group:${group.id}`)}
              onOpenMembers={(item) => void square.openGroupMembers(item)}
              onShare={(item) => square.share('group', item.id)}
              onCreateSession={square.createGroupSession}
            />
          ))}
        </div>
      )}
      {!square.loading && !square.error && square.visibleGroups.length > 0 && square.hasMore && (
        <div ref={sentinelRef} aria-hidden="true" className="h-1" />
      )}
      {!square.loading && !square.error && square.loadingMore && (
        <div aria-live="polite" className="text-center text-xs text-muted-foreground">
          正在加载更多...
        </div>
      )}
      {!square.loading && !square.error && square.loadMoreError && (
        <Card className="flex items-center justify-between gap-3 p-4">
          <p className="m-0 text-sm text-muted-foreground">{square.loadMoreError}</p>
          <Button variant="secondary" size="sm" onClick={() => void square.loadMore()}>
            重试
          </Button>
        </Card>
      )}
      <GroupMembersModal
        open={Boolean(square.selectedGroupId)}
        group={square.selectedGroup}
        members={square.groupMembers}
        loading={square.detailLoading}
        onClose={square.closeGroupMembers}
      />
      <CreateGroupSessionModal
        open={Boolean(square.createSessionTarget)}
        group={square.createSessionTarget}
        loading={square.isCreatingSession}
        onClose={square.closeCreateSessionModal}
        onSubmit={square.submitCreateSession}
      />
    </>
  );
}
