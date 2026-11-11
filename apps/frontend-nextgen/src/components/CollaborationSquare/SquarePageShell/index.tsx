// 完整页面门面：旧任务入口保留；公共 Bot/群路由使用无任务依赖的 PublicDiscoveryPageShell。
import { PublicDiscoveryPageContent } from '@/components/CollaborationSquare/PublicDiscoveryPageShell';
import { PublicTaskCatalogPanel, type TaskCatalogViewModel } from '@/components/CollaborationSquare/PublicTaskCatalogPanel';
import { SquareNavigation } from '@/components/CollaborationSquare/SquareNavigation';
import { PageHeader } from '@/components/Common/PageHeader';
import type { SquareResource } from '@/domain/collaborationSquare/types';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { useCallback, type UIEvent } from 'react';

export interface SquarePageShellProps { resource: SquareResource; }
export function SquarePageShell({ resource }: SquarePageShellProps) {
  const square = useCollaborationSquare(resource);
  const handleLoadMore = useCallback(() => square.loadMore(), [square.loadMore]);
  const canLoadMore = square.hasMore && !square.loading && !square.loadingMore && !square.error;
  const handleScroll = (event: UIEvent<HTMLElement>) => {
    const target = event.currentTarget;
    if (canLoadMore && target.scrollHeight - target.scrollTop - target.clientHeight <= 420) void handleLoadMore();
  };
  const taskViewModel: TaskCatalogViewModel = {
    tasks: square.tasks,
    taskQuery: square.taskQuery,
    taskStatusFilter: square.taskStatusFilter,
    setTaskQuery: (query) => square.setTaskQuery(query),
    setTaskStatusFilter: square.setTaskStatusFilter,
    resetTaskFilters: square.resetTaskFilters,
    loading: square.loading,
    error: square.error,
    hasMore: square.hasMore,
    loadingMore: square.loadingMore,
    loadMore: handleLoadMore,
    loadMoreError: square.loadMoreError,
    reload: () => square.load(),
    openTaskDetail: square.openTaskDetail,
    selectedTaskId: square.selectedTaskId,
    taskDetail: square.taskDetail,
    detailLoading: square.detailLoading,
    closeTaskDetail: square.closeTaskDetail,
  };

  if (resource !== 'task') return <PublicDiscoveryPageContent resource={resource} square={square} />;
  return (
    <div className="flex h-full flex-col bg-muted">
      <SquareNavigation active="bot" />
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto" onScroll={handleScroll}>
        <div className="flex w-full flex-col p-4 sm:p-6 lg:p-8">
          <section className="space-y-4" aria-label="任务广场内容">
            <PageHeader title="任务广场" description="发现公开 BBS 求助任务，按关键词与状态筛选适合当前协作目标的任务。" />
            <PublicTaskCatalogPanel vm={taskViewModel} />
          </section>
        </div>
      </main>
    </div>
  );
}
