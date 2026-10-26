import { PublicBotCatalogPanel } from '@/components/CollaborationSquare/PublicBotCatalogPanel';
import { PublicGroupSquareSection } from '@/components/CollaborationSquare/PublicGroupSquareSection';
import {
  PublicTaskCatalogPanel,
  type TaskCatalogViewModel,
} from '@/components/CollaborationSquare/PublicTaskCatalogPanel';
import { SquareNavigation } from '@/components/CollaborationSquare/SquareNavigation';
import { PageHeader } from '@/components/Common/PageHeader';
import type { BotCatalogViewModel, SquareResource } from '@/domain/collaborationSquare/types';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { type UIEvent, useCallback, useRef } from 'react';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { TrackExpo } from '@/tracking/TrackExpo';

const LOAD_MORE_PRELOAD_DISTANCE = 420;
const TASK_DESCRIPTION = '发现公开 BBS 求助任务，按关键词与状态筛选适合当前协作目标的任务。';

export function SquarePageShell({ resource }: { resource: SquareResource }) {
  const square = useCollaborationSquare(resource);
  const scrollRootRef = useRef<HTMLElement>(null);
  const canLoadMore = square.hasMore && !square.loading && !square.loadingMore && !square.error;
  const handleScroll = useCallback(
    (event: UIEvent<HTMLElement>) => {
      const target = event.currentTarget;
      const distanceToBottom = target.scrollHeight - target.scrollTop - target.clientHeight;
      if (canLoadMore && distanceToBottom <= LOAD_MORE_PRELOAD_DISTANCE) void square.loadMore();
    },
    [canLoadMore, square.loadMore],
  );

  const botViewModel: BotCatalogViewModel = {
    activeActor: square.activeActor,
    activeActorName: square.activeActorName,
    bots: square.visibleBots,
    busyKeys: square.busyKeys,
    query: square.botQuery,
    mode: square.botSearchMode,
    loading: square.loading,
    error: square.error,
    hasMore: square.hasMore,
    loadingMore: square.loadingMore,
    loadMoreError: square.loadMoreError,
    // 广场搜索(add-spm-tracking):空→非空首帧计一次「搜索」;即时过滤不逐键上报。
    setQuery: (query) => {
      if (resource === 'bot' && !square.botQuery && query) Tracert.click(Points.collabSquare.search);
      square.setQuery('bot', query);
    },
    setMode: square.setBotSearchMode,
    reload: () => square.load(),
    loadMore: () => square.loadMore(),
    primaryAction: square.primaryBotAction,
    share: (bot) => square.share('bot', bot.id, bot.name),
    openProfile: square.openBotProfile,
    closeProfile: square.closeBotProfile,
    selectedBotId: square.selectedBotId,
    botProfile: square.botProfile,
    detailLoading: square.detailLoading,
    copyBotId: square.copyBotId,
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
    loadMore: () => square.loadMore(),
    loadMoreError: square.loadMoreError,
    reload: () => square.load(),
    openTaskDetail: square.openTaskDetail,
    selectedTaskId: square.selectedTaskId,
    taskDetail: square.taskDetail,
    detailLoading: square.detailLoading,
    closeTaskDetail: square.closeTaskDetail,
  };

  const resourceTitle = resource === 'bot' ? '公开 Bot' : resource === 'group' ? '公开协作群' : '任务广场';

  return (
    <div className={`flex h-full flex-col ${resource !== 'task' ? 'bg-background' : 'bg-muted'}`}>
      <SquareNavigation active={resource === 'group' ? 'group' : 'bot'} />
      <main ref={scrollRootRef} className="app-scrollbar min-h-0 flex-1 overflow-y-auto" onScroll={handleScroll}>
        <div className={`mx-auto flex w-full max-w-7xl flex-col ${
          resource !== 'task' ? 'px-4 pb-4 pt-3 sm:px-6 sm:pb-6 lg:px-8 lg:pb-8' : 'p-4 sm:p-6 lg:p-8'
        }`}>
          <section className={resource !== 'task' ? 'space-y-5' : 'space-y-4'} aria-label={`${resourceTitle}内容`}>
            {resource === 'task' && <PageHeader title={resourceTitle} description={TASK_DESCRIPTION} />}
            {resource === 'bot' && <TrackExpo point={Points.collabSquare.heroExpo} />}
            {resource === 'bot' && (
              <PublicBotCatalogPanel
                vm={botViewModel}
                scrollRootRef={scrollRootRef}
                smartEmptyHint="请输入关键词进行智能搜索"
              />
            )}
            {resource === 'group' && (
              <PublicGroupSquareSection square={square} scrollRootRef={scrollRootRef} canLoadMore={canLoadMore} />
            )}
            {resource === 'task' && <PublicTaskCatalogPanel vm={taskViewModel} />}
          </section>
        </div>
      </main>
    </div>
  );
}
