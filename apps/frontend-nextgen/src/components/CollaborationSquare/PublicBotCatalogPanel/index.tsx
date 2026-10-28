import { FriendRequestConfirmModal } from '@/components/CollaborationSquare/FriendRequestConfirmModal';
import SquareBotCard from '@/components/CollaborationSquare/BotCard';
import { BotProfileModal } from '@/components/CollaborationSquare/BotProfileModal';
import { SquareIdentityPicker } from '@/components/CollaborationSquare/SquareIdentityPicker';
import SquareSearchBar from '@/components/CollaborationSquare/SquareSearchBar';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { BotCatalogViewModel, PublicBot } from '@/domain/collaborationSquare/types';
import { getPublicBotActionKey, getPublicBotTargetId, resolvePublicBotPrimaryAction } from '@/domain/collaborationSquare/types';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { Bot, RefreshCw, Search } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

export interface PublicBotCatalogPanelProps {
  /** 面板展示层 view model，由协作广场 store hook 产出。 */
  vm: BotCatalogViewModel;
  /** 滚动容器 ref，用于无限滚动 IntersectionObserver 的 root。 */
  scrollRootRef: React.RefObject<HTMLElement>;
  /**
   * 透传时启用智能搜索空关键词 gating：mode==='smart' 且未输入关键词时只展示该提示，
   * 不发请求、不展示列表。协作广场页不透传（保持空关键词加载默认目录）。
   */
  smartEmptyHint?: string;
}

function BotLoadingState() {
  return (
    <div aria-label="正在加载公开 Bot" className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
      {[0, 1, 2].map((item) => (
        <Card key={item}>
          <Skeleton.Card />
        </Card>
      ))}
    </div>
  );
}

/**
 * 公开 Bot 面板公共展示组件：搜索栏 + 加载/错误/空/智能空提示态 + 卡片网格 + 无限滚动 + 画像弹层。
 * 由协作广场页（`SquarePageShell` bot 分支）使用，数据由调用方以 {@link BotCatalogViewModel} 形式注入。
 */
export function PublicBotCatalogPanel({ vm, scrollRootRef, smartEmptyHint }: PublicBotCatalogPanelProps) {
  const sentinelRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const highlightBotId = useCollaborationSquareStore((state) => state.highlightBotId);
  const { hasMore, loading, loadingMore, error, loadMore } = vm;
  const activeActor = vm.activeActor;
  const actorKey = activeActor ? `${activeActor.type}:${activeActor.id}` : '';
  const [pending, setPending] = useState<{ bot: PublicBot; actorKey: string } | null>(null);
  // 身份变化后必须重新确认，不能用旧弹窗为新身份发送申请。
  useEffect(() => setPending(null), [actorKey]);
  // 深链定位：滚动到目标卡片并短暂高亮，数秒后自动清除。
  useEffect(() => {
    if (!highlightBotId) return;
    const target = gridRef.current?.querySelector<HTMLElement>(`[data-bot-id="${CSS.escape(highlightBotId)}"]`);
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const timer = setTimeout(() => useCollaborationSquareStore.getState().setHighlightBotId(null), 3000);
    return () => clearTimeout(timer);
  }, [highlightBotId]);
  const pendingBot = pending?.actorKey === actorKey
    ? vm.bots.find((bot) => getPublicBotTargetId(bot) === getPublicBotTargetId(pending.bot))
    : undefined;
  const canRequest = (bot: PublicBot) => Boolean(activeActor &&
    !vm.busyKeys.includes(getPublicBotActionKey(activeActor, getPublicBotTargetId(bot))) &&
    resolvePublicBotPrimaryAction({
      activeActor,
      targetActorId: getPublicBotTargetId(bot),
      relationshipStatus: bot.relationshipStatus,
      isOwnedByLoggedInUser: bot.isOwnedByLoggedInUser,
    }) === 'request_friendship');
  const requestTarget = pendingBot && canRequest(pendingBot) ? pendingBot : null;
  const handlePrimaryAction = (bot: PublicBot) => {
    if (canRequest(bot)) setPending({ bot, actorKey });
    else vm.primaryAction(bot);
  };
  const smartEmpty = Boolean(smartEmptyHint) && vm.mode === 'smart' && !vm.query.trim();
  const showGrid = Boolean(activeActor) && !loading && !error && !smartEmpty && vm.bots.length > 0;

  useEffect(() => {
    if (smartEmpty || !hasMore || loading || loadingMore || error) return;
    const root = scrollRootRef.current;
    const sentinel = sentinelRef.current;
    if (!root || !sentinel || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) loadMore();
      },
      { root, rootMargin: '0px 0px 420px' },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [scrollRootRef, smartEmpty, hasMore, loading, loadingMore, error, loadMore]);

  return (
    <>
      <div className="flex flex-col items-end gap-3 md:flex-row md:items-center md:justify-end">
        <SquareSearchBar
          resource="bot"
          query={vm.query}
          mode={vm.mode}
          onQueryChange={vm.setQuery}
          onModeChange={vm.setMode}
          className="w-full md:max-w-md"
        />
        <div className="w-full max-w-[280px] md:w-[280px] md:shrink-0">
          <SquareIdentityPicker />
        </div>
      </div>
      {loading && <BotLoadingState />}
      {!loading && error && (
        <Card>
          <Empty
            title="公开 Bot 加载失败"
            description={error}
            action={
              <Button onClick={vm.reload} leftIcon={<RefreshCw aria-hidden className="h-4 w-4" />}>
                重新加载
              </Button>
            }
          />
        </Card>
      )}
      {!loading && !error && smartEmpty && (
        <Card>
          <Empty
            title={smartEmptyHint ?? '请输入关键词'}
            description="输入能力或职责描述后，将智能搜索匹配的公开 Bot。"
            icon={<Search aria-hidden className="h-5 w-5" />}
          />
        </Card>
      )}
      {!loading && !error && !smartEmpty && vm.bots.length === 0 && (
        <Card>
          <Empty
            title="没有找到公开 Bot"
            description="尝试更换关键词或清除搜索。"
            icon={<Bot aria-hidden className="h-5 w-5" />}
          />
        </Card>
      )}
      {showGrid && activeActor && (
        <div ref={gridRef} className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {vm.bots.map((bot) => (
            <SquareBotCard
              key={getPublicBotTargetId(bot)}
              bot={bot}
              activeActor={activeActor}
              busy={vm.busyKeys.includes(getPublicBotActionKey(activeActor, getPublicBotTargetId(bot)))}
              highlighted={bot.id === highlightBotId}
              onShare={(item) => vm.share(item)}
              onPrimaryAction={(item) => {
                Tracert.click(Points.collabSquare.openBotCard, { bot_id: getPublicBotTargetId(item) });
                handlePrimaryAction(item);
              }}
            />
          ))}
        </div>
      )}
      {showGrid && hasMore && <div ref={sentinelRef} aria-hidden="true" className="h-1" />}
      {!loading && !error && !smartEmpty && loadingMore && (
        <div aria-live="polite" className="text-center text-xs text-muted-foreground">
          正在加载更多...
        </div>
      )}
      {!loading && !error && !smartEmpty && vm.loadMoreError && (
        <Card className="flex items-center justify-between gap-3 p-4">
          <p className="m-0 text-sm text-muted-foreground">{vm.loadMoreError}</p>
          <Button variant="secondary" size="sm" onClick={loadMore}>
            重试
          </Button>
        </Card>
      )}
      {requestTarget && activeActor && (
        <FriendRequestConfirmModal
          bot={requestTarget}
          actor={activeActor}
          actorName={vm.activeActorName || activeActor.id}
          onClose={() => setPending(null)}
          onConfirm={() => {
            setPending(null);
            vm.primaryAction(requestTarget);
          }}
        />
      )}
      <BotProfileModal
        open={Boolean(vm.selectedBotId)}
        profile={vm.botProfile}
        loading={vm.detailLoading}
        onClose={vm.closeProfile}
        onCopyId={(id) => vm.copyBotId(id)}
      />
    </>
  );
}
