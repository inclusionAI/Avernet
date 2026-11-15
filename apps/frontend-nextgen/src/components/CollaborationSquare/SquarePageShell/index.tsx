import { PublicBotCatalogPanel } from '@/components/CollaborationSquare/PublicBotCatalogPanel';
import { GroupIdentityDisplay } from '@/components/CollaborationSquare/PublicGroupSquareSection/GroupIdentityDisplay';
import { PublicGroupSquareSection } from '@/components/CollaborationSquare/PublicGroupSquareSection';
import {
  PublicTaskCatalogPanel,
  type TaskCatalogViewModel,
} from '@/components/CollaborationSquare/PublicTaskCatalogPanel';
import { SquareNavigation } from '@/components/CollaborationSquare/SquareNavigation';
import { SquareIdentityPicker } from '@/components/CollaborationSquare/SquareIdentityPicker';
import SquareSearchBar from '@/components/CollaborationSquare/SquareSearchBar';
import { PageHeader } from '@/components/Common/PageHeader';
import type { BotCatalogViewModel, SquareResource } from '@/domain/collaborationSquare/types';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { type UIEvent, useCallback, useRef } from 'react';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { TrackExpo } from '@/tracking/TrackExpo';

const LOAD_MORE_PRELOAD_DISTANCE = 420;
const TASK_DESCRIPTION = '发现公开 BBS 求助任务，按关键词与状态筛选适合当前协作目标的任务。';

export interface SquarePageShellProps {
  resource: SquareResource;
}

export function SquarePageShell({ resource }: SquarePageShellProps) {
  const square = useCollaborationSquare(resource);
  const scrollRootRef = useRef<HTMLElement>(null);
  const canLoadMore = square.hasMore && !square.loading && !square.loadingMore && !square.error;
  // 稳定引用：面板把 loadMore 写进 IntersectionObserver effect 依赖，逐帧新建箭头函数会反复重建 observer。
  const handleLoadMore = useCallback(() => square.loadMore(), [square.loadMore]);
  const handleScroll = useCallback(
    (event: UIEvent<HTMLElement>) => {
      const target = event.currentTarget;
      const distanceToBottom = target.scrollHeight - target.scrollTop - target.clientHeight;
      if (canLoadMore && distanceToBottom <= LOAD_MORE_PRELOAD_DISTANCE) void handleLoadMore();
    },
    [canLoadMore, handleLoadMore],
  );

  const botViewModel: BotCatalogViewModel = {
    activeActor: square.activeActor,
    activeActorName: square.activeActorName,
    bots: square.visibleBots,
    busyKeys: square.busyKeys,
    query: square.botQuery,
    mode: square.botSearchMode,
    searchResultMode: square.searchResultMode,
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
    loadMore: handleLoadMore,
    primaryAction: square.primaryBotAction,
    share: (bot) => square.share('bot', bot.id, bot.name, bot.audience),
    openProfile: square.openBotProfile,
    closeProfile: square.closeBotProfile,
    selectedBotId: square.selectedBotId,
    botProfile: square.botProfile,
    botProfileError: square.botProfileError,
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
    loadMore: handleLoadMore,
    loadMoreError: square.loadMoreError,
    reload: () => square.load(),
    openTaskDetail: square.openTaskDetail,
    selectedTaskId: square.selectedTaskId,
    taskDetail: square.taskDetail,
    detailLoading: square.detailLoading,
    closeTaskDetail: square.closeTaskDetail,
  };

  const resourceTitle = resource === 'bot' ? '公开 Bot' : resource === 'group' ? '公开协作群' : '任务广场';

  // dmore Frame 2：搜索栏(348px) + 身份展示挂到页头右端；群 Tab 同规格上移页头
  //（搜索 348px + 固定登录身份展示 222px，artboard-004），内容区不再重复出现工具栏。
  // 身份选择器与下拉弹层（dmore artboard-002 弹层 338px）保持等宽（222 提至 338）。
  const headerActions =
    resource === 'bot' ? (
      <>
        <SquareSearchBar
          resource="bot"
          query={botViewModel.query}
          mode={botViewModel.mode}
          searchResultMode={botViewModel.searchResultMode}
          onQueryChange={botViewModel.setQuery}
          onModeChange={botViewModel.setMode}
          className="w-full min-w-0 md:w-[348px]"
        />
        {/* min-w-0 允许窄窗口收缩（内容 truncate），避免定宽把页头顶出页面。 */}
        <div className="w-full min-w-0 md:w-[338px]">
          <SquareIdentityPicker />
        </div>
      </>
    ) : resource === 'group' ? (
      <>
        <SquareSearchBar
          resource="group"
          query={square.groupQuery}
          onQueryChange={(query) => square.setQuery('group', query)}
          className="w-full min-w-0 md:w-[348px]"
        />
        <div className="w-full min-w-0 md:w-[222px]">
          <GroupIdentityDisplay />
        </div>
      </>
    ) : undefined;

  return (
    <div className={`flex h-full flex-col ${resource !== 'task' ? 'bg-background' : 'bg-muted'}`}>
      <SquareNavigation active={resource === 'group' ? 'group' : 'bot'} actions={headerActions} />
      <main ref={scrollRootRef} className="app-scrollbar min-h-0 flex-1 overflow-y-auto" onScroll={handleScroll}>
        <div className={`flex w-full flex-col ${
          resource !== 'task' ? 'px-5 pb-6 pt-4' : 'p-4 sm:p-6 lg:p-8'
        }`}>
          <section className={resource !== 'task' ? 'space-y-4' : 'space-y-4'} aria-label={`${resourceTitle}内容`}>
            {resource === 'task' && <PageHeader title={resourceTitle} description={TASK_DESCRIPTION} />}
            {resource === 'bot' && <TrackExpo point={Points.collabSquare.heroExpo} />}
            {resource === 'bot' && (
              <PublicBotCatalogPanel
                vm={botViewModel}
                scrollRootRef={scrollRootRef}
                smartEmptyHint="请输入关键词进行智能搜索"
              />
            )}
            {resource === 'group' && <PublicGroupSquareSection square={square} scrollRootRef={scrollRootRef} canLoadMore={canLoadMore} />}
            {resource === 'task' && <PublicTaskCatalogPanel vm={taskViewModel} />}
          </section>
        </div>
      </main>
    </div>
  );
}
