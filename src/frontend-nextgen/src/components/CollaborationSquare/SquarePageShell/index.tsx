import { PublicBotCatalogPanel } from '@/components/CollaborationSquare/PublicBotCatalogPanel';
import { PublicGroupSquareSection } from '@/components/CollaborationSquare/PublicGroupSquareSection';
import {
  PublicTaskCatalogPanel,
  type TaskCatalogViewModel,
} from '@/components/CollaborationSquare/PublicTaskCatalogPanel';
import { PageHeader } from '@/components/Common/PageHeader';
import type { BotCatalogViewModel, SquareResource } from '@/domain/collaborationSquare/types';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { history, Link } from '@umijs/max';
import { type MouseEvent, type UIEvent, useCallback, useEffect, useRef, useState } from 'react';

const LOAD_MORE_PRELOAD_DISTANCE = 420;
const TAB_TRANSITION_DURATION_MS = 200;

const BOT_DESCRIPTION =
  '可按 Bot 名称或 Owner 用户名称搜索公开 Bot，也可通过能力描述进行智能发现，并以当前用户身份发起好友申请。';
const GROUP_DESCRIPTION = '发现协作群，支持基于公开协作群快速创建新会话。';
const TASK_DESCRIPTION = '发现公开 BBS 求助任务，按关键词与状态筛选适合当前协作目标的任务。';

export function SquarePageShell({ resource }: { resource: SquareResource }) {
  const square = useCollaborationSquare(resource);
  const scrollRootRef = useRef<HTMLElement>(null);
  const navigationTimerRef = useRef<number>();
  const [visualResource, setVisualResource] = useState(resource);
  const canLoadMore = square.hasMore && !square.loading && !square.loadingMore && !square.error;

  useEffect(() => {
    setVisualResource(resource);
  }, [resource]);

  useEffect(
    () => () => {
      if (navigationTimerRef.current !== undefined) window.clearTimeout(navigationTimerRef.current);
    },
    [],
  );

  const handleResourceNavigation = useCallback(
    (event: MouseEvent<HTMLAnchorElement>, nextResource: SquareResource, path: string) => {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey ||
        event.currentTarget.target === '_blank' ||
        event.currentTarget.hasAttribute('download')
      ) {
        return;
      }

      if (nextResource === resource) {
        if (visualResource !== resource) {
          event.preventDefault();
          if (navigationTimerRef.current !== undefined) window.clearTimeout(navigationTimerRef.current);
          navigationTimerRef.current = undefined;
          setVisualResource(resource);
        }
        return;
      }

      event.preventDefault();
      if (navigationTimerRef.current !== undefined) window.clearTimeout(navigationTimerRef.current);
      setVisualResource(nextResource);
      navigationTimerRef.current = window.setTimeout(() => {
        history.push(path);
      }, TAB_TRANSITION_DURATION_MS);
    },
    [resource, visualResource],
  );
  const handleScroll = useCallback(
    (event: UIEvent<HTMLElement>) => {
      const target = event.currentTarget;
      const distanceToBottom = target.scrollHeight - target.scrollTop - target.clientHeight;
      if (canLoadMore && distanceToBottom <= LOAD_MORE_PRELOAD_DISTANCE) void square.loadMore();
    },
    [canLoadMore, square.loadMore],
  );

  const botViewModel: BotCatalogViewModel = {
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
      <nav
        className="shrink-0 border-b border-border bg-background/80 px-4 backdrop-blur-md sm:px-6"
        aria-label="协作广场资源导航"
      >
        <div className="mx-auto flex w-full max-w-7xl items-stretch gap-8">
          <Link
            to="/collaboration-square/bots"
            aria-current={resource === 'bot' ? 'page' : undefined}
            onClick={(event) => handleResourceNavigation(event, 'bot', '/collaboration-square/bots')}
            className={`relative flex h-[54px] items-center px-1 text-sm transition-colors hover:text-primary ${
              visualResource === 'bot' ? 'font-medium text-foreground' : 'font-normal text-muted-foreground'
            }`}
          >
            公开 Bot
            <span
              className={`absolute inset-x-0 bottom-0 h-[3px] rounded-t-full bg-primary transition-transform duration-200 ease-out ${
                visualResource === 'bot' ? 'scale-x-100' : 'scale-x-0'
              }`}
              aria-hidden
            />
          </Link>
          <Link
            to="/collaboration-square/groups"
            aria-current={resource === 'group' ? 'page' : undefined}
            onClick={(event) => handleResourceNavigation(event, 'group', '/collaboration-square/groups')}
            className={`relative flex h-[54px] items-center px-1 text-sm transition-colors hover:text-primary ${
              visualResource === 'group' ? 'font-medium text-foreground' : 'font-normal text-muted-foreground'
            }`}
          >
            公开协作群
            <span
              className={`absolute inset-x-0 bottom-0 h-[3px] rounded-t-full bg-primary transition-transform duration-200 ease-out ${
                visualResource === 'group' ? 'scale-x-100' : 'scale-x-0'
              }`}
              aria-hidden
            />
          </Link>
          <Link
            to="/collaboration-square/tasks"
            aria-current={resource === 'task' ? 'page' : undefined}
            onClick={(event) => handleResourceNavigation(event, 'task', '/collaboration-square/tasks')}
            className={`relative flex h-[54px] items-center px-1 text-sm transition-colors hover:text-primary ${
              visualResource === 'task' ? 'font-medium text-foreground' : 'font-normal text-muted-foreground'
            }`}
          >
            任务广场
            <span
              className={`absolute inset-x-0 bottom-0 h-[3px] rounded-t-full bg-primary transition-transform duration-200 ease-out ${
                visualResource === 'task' ? 'scale-x-100' : 'scale-x-0'
              }`}
              aria-hidden
            />
          </Link>
        </div>
      </nav>
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
