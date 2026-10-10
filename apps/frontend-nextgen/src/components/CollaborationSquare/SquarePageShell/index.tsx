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

const LOAD_MORE_PRELOAD_DISTANCE = 420;
const BOT_DESCRIPTION =
  '可按 Bot 名称或 Owner 用户名称搜索公开 Bot，也可通过能力描述进行智能搜索，为我或我的 Bot 申请加对方 Bot 为好友。';
const GROUP_DESCRIPTION = '发现协作群，支持基于公开协作群快速创建新会话。';
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
    bots: square.visibleBots,
    busyKeys: square.busyKeys,
    query: square.botQuery,
    mode: square.botSearchMode,
    loading: square.loading,
    error: square.error,
    hasMore: square.hasMore,
    loadingMore: square.loadingMore,
    loadMoreError: square.loadMoreError,
    setQuery: (query) => square.setQuery('bot', query),
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
  const description =
    resource === 'bot' ? BOT_DESCRIPTION : resource === 'group' ? GROUP_DESCRIPTION : TASK_DESCRIPTION;

  return (
    <div className="flex h-full flex-col bg-muted">
      <SquareNavigation active={resource === 'group' ? 'group' : 'bot'} />
      <main ref={scrollRootRef} className="app-scrollbar min-h-0 flex-1 overflow-y-auto" onScroll={handleScroll}>
        <div className="mx-auto flex w-full max-w-7xl flex-col p-4 sm:p-6 lg:p-8">
          <section className="space-y-4" aria-label={`${resourceTitle}内容`}>
            <PageHeader title={resourceTitle} description={description} />
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
