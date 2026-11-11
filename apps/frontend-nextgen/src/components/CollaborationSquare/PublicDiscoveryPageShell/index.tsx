import { PublicBotCatalogPanel } from '@/components/CollaborationSquare/PublicBotCatalogPanel';
import { GroupIdentityDisplay } from '@/components/CollaborationSquare/PublicGroupSquareSection/GroupIdentityDisplay';
import { PublicGroupSquareSection } from '@/components/CollaborationSquare/PublicGroupSquareSection';
import { SquareNavigation } from '@/components/CollaborationSquare/SquareNavigation';
import { SquareIdentityPicker } from '@/components/CollaborationSquare/SquareIdentityPicker';
import SquareSearchBar from '@/components/CollaborationSquare/SquareSearchBar';
import type { BotCatalogViewModel } from '@/domain/collaborationSquare/types';
import { usePublicDiscovery } from '@/hooks/usePublicDiscovery';
import { type UIEvent, useCallback, useRef } from 'react';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { TrackExpo } from '@/tracking/TrackExpo';

const LOAD_MORE_PRELOAD_DISTANCE = 420;

export interface SquarePageShellProps {
  resource: 'bot' | 'group';
}

export function PublicDiscoveryPageContent({ resource, square }: SquarePageShellProps & { square: ReturnType<typeof usePublicDiscovery> }) {
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

  const resourceTitle = resource === 'bot' ? '公开 Bot' : '公开协作群';

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
    <div className="flex h-full flex-col bg-background">
      <SquareNavigation active={resource === 'group' ? 'group' : 'bot'} actions={headerActions} />
      <main ref={scrollRootRef} className="app-scrollbar min-h-0 flex-1 overflow-y-auto" onScroll={handleScroll}>
        <div className="flex w-full flex-col px-5 pb-6 pt-4">
          <section className="space-y-4" aria-label={`${resourceTitle}内容`}>
            {resource === 'bot' && <TrackExpo point={Points.collabSquare.heroExpo} />}
            {resource === 'bot' && (
              <PublicBotCatalogPanel
                vm={botViewModel}
                scrollRootRef={scrollRootRef}
                smartEmptyHint="请输入关键词进行智能搜索"
              />
            )}
            {resource === 'group' && <PublicGroupSquareSection square={square} scrollRootRef={scrollRootRef} canLoadMore={canLoadMore} />}
          </section>
        </div>
      </main>
    </div>
  );
}

export function PublicDiscoveryPageShell({ resource }: SquarePageShellProps) {
  const square = usePublicDiscovery(resource);
  return <PublicDiscoveryPageContent resource={resource} square={square} />;
}
