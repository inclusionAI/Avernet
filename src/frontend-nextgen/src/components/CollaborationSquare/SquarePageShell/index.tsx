import { CreateGroupSessionModal } from '@/components/CollaborationSquare/CreateGroupSessionModal';
import GroupCard from '@/components/CollaborationSquare/GroupCard';
import { GroupMembersModal } from '@/components/CollaborationSquare/GroupMembersModal';
import { PublicBotCatalogPanel } from '@/components/CollaborationSquare/PublicBotCatalogPanel';
import SquareSearchBar from '@/components/CollaborationSquare/SquareSearchBar';
import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { BotCatalogViewModel, SquareResource } from '@/domain/collaborationSquare/types';
import { useCollaborationSquare } from '@/hooks/useCollaborationSquare';
import { history } from '@umijs/max';
import { Bot, RefreshCw, Users } from 'lucide-react';
import { type UIEvent, useCallback, useEffect, useRef } from 'react';

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

export function SquarePageShell({ resource }: { resource: SquareResource }) {
  const square = useCollaborationSquare(resource);
  const isBot = resource === 'bot';
  const scrollRootRef = useRef<HTMLElement>(null);
  const groupSentinelRef = useRef<HTMLDivElement>(null);
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
    share: (bot) => square.share('bot', bot.id),
    openProfile: square.openBotProfile,
    closeProfile: square.closeBotProfile,
    selectedBotId: square.selectedBotId,
    botProfile: square.botProfile,
    detailLoading: square.detailLoading,
    copyBotId: square.copyBotId,
  };

  useEffect(() => {
    if (isBot) return;
    const root = scrollRootRef.current;
    const sentinel = groupSentinelRef.current;
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
  }, [canLoadMore, isBot, square.loadMore]);
  return (
    <main ref={scrollRootRef} className="app-scrollbar h-full overflow-y-auto" onScroll={handleScroll}>
      <div className="mx-auto flex w-full max-w-7xl flex-col space-y-5 p-4 sm:p-6 lg:p-8">
        <PageHeader
          title="协作广场"
          description={
            isBot
              ? '可按 Bot 名称或 Owner 用户名称搜索公开 Bot，也可通过能力描述进行智能发现，并以当前用户身份发起好友申请。'
              : '发现协作群，支持基于公开协作群快速创建新会话。'
          }
        />
        <div className="flex flex-wrap gap-2" aria-label="协作广场资源导航">
          <Button
            variant={isBot ? 'primary' : 'secondary'}
            onClick={() => history.push('/collaboration-square/bots')}
            leftIcon={<Bot aria-hidden className="h-4 w-4" />}
          >
            公开 Bot
          </Button>
          <Button
            variant={!isBot ? 'primary' : 'secondary'}
            onClick={() => history.push('/collaboration-square/groups')}
            leftIcon={<Users aria-hidden className="h-4 w-4" />}
          >
            公开协作群
          </Button>
        </div>
        {isBot ? (
          <PublicBotCatalogPanel
            vm={botViewModel}
            scrollRootRef={scrollRootRef}
            smartEmptyHint="请输入关键词进行智能搜索"
          />
        ) : (
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
              <div ref={groupSentinelRef} aria-hidden="true" className="h-1" />
            )}
            {!square.loading && !square.error && square.loadingMore && (
              <div aria-live="polite" className="text-center text-xs text-[var(--color-muted)]">
                正在加载更多...
              </div>
            )}
            {!square.loading && !square.error && square.loadMoreError && (
              <Card className="flex items-center justify-between gap-3 p-4">
                <p className="m-0 text-sm text-[var(--color-muted)]">{square.loadMoreError}</p>
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
        )}
      </div>
    </main>
  );
}
