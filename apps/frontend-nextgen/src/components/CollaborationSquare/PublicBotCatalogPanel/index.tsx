import SquareBotCard, { SquareBotCardSkeleton } from '@/components/CollaborationSquare/BotCard';
import { FriendRequestDialog } from '@/components/CollaborationSquare/FriendRequestDialog';
import { BotProfileModal } from '@/components/CollaborationSquare/BotProfileModal';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import type { BotCatalogViewModel, PublicBot } from '@/domain/collaborationSquare/types';
import { getPublicBotActionKey, getPublicBotTargetId, resolvePublicBotPrimaryAction } from '@/domain/collaborationSquare/types';
import { useSquareHighlightStore } from '@/stores/squareHighlightStore';
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
    <div aria-label="正在加载公开 Bot" className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {[0, 1, 2, 3, 4, 5].map((item) => (
        <SquareBotCardSkeleton key={item} />
      ))}
    </div>
  );
}

/**
 * 公开 Bot 面板公共展示组件：加载/错误/空/智能空提示态 + 卡片网格 + 无限滚动 + 画像弹层。
 * 搜索栏与身份选择器已上移至页头（`SquarePageShell` → `SquareNavigation` actions），
 * 数据由调用方以 {@link BotCatalogViewModel} 形式注入。
 */
export function PublicBotCatalogPanel({ vm, scrollRootRef, smartEmptyHint }: PublicBotCatalogPanelProps) {
  const sentinelRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const highlightBotId = useSquareHighlightStore((state) => state.highlightBotId);
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
    const timer = setTimeout(() => useSquareHighlightStore.getState().setHighlightBotId(null), 3000);
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
  // 申请弹窗详情（引擎 chip 与已公开能力）与画像弹窗共用 store 状态；按 selectedBotId 对齐后才可用。
  const requestProfile = requestTarget && vm.selectedBotId === requestTarget.id ? vm.botProfile : null;
  // 画像拉取中（弹窗内 shimmer 占位）；失败原因改在弹窗内提示（不 toast），同口径按 selectedBotId 归口。
  const requestProfileLoading = requestTarget && vm.selectedBotId === requestTarget.id ? vm.detailLoading : false;
  const requestProfileError = requestTarget && vm.selectedBotId === requestTarget.id ? vm.botProfileError : null;
  const closeRequestDialog = () => {
    setPending(null);
    vm.closeProfile();
  };
  const handlePrimaryAction = (bot: PublicBot) => {
    if (canRequest(bot)) {
      setPending({ bot, actorKey });
      // Catalog 摘要不含引擎与已公开能力，弹窗展示所需的详情由画像接口补齐。
      vm.openProfile(bot);
    } else {
      vm.primaryAction(bot);
    }
  };
  const smartEmpty = Boolean(smartEmptyHint) && vm.mode === 'smart' && !vm.query.trim();
  const showGrid = Boolean(activeActor) && !loading && !error && !smartEmpty && vm.bots.length > 0;
  // 智能搜索降级为名称匹配：结果上方明示回退来源（BCS search_mode=name_fallback）。
  const showNameFallbackHint = showGrid && vm.searchResultMode === 'name_fallback';

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
      {showGrid && showNameFallbackHint && (
        <p className="m-0 text-xs text-muted-foreground">未命中语义匹配，已按名称匹配展示结果</p>
      )}
      {showGrid && activeActor && (
        // dmore：3 列卡片网格，16px 间距（页头下 16px 起）。
        <div ref={gridRef} data-guide-target="discover-public-bot-list" className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
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
      {/* 申请好友权限弹窗（dmore artboard-003 无能力 / artboard-007 有能力）：
          无能力版按钮沉底、有能力版按钮挂头部右端并展示「已公开能力」清单。 */}
      {requestTarget && activeActor && (
        <FriendRequestDialog
          bot={requestTarget}
          profile={requestProfile}
          profileLoading={requestProfileLoading}
          profileError={requestProfileError}
          onClose={closeRequestDialog}
          onSendRequest={() => {
            closeRequestDialog();
            vm.primaryAction(requestTarget);
          }}
        />
      )}
      <BotProfileModal
        open={Boolean(vm.selectedBotId) && !requestTarget}
        profile={vm.botProfile}
        loading={vm.detailLoading}
        error={vm.botProfileError}
        onClose={vm.closeProfile}
        onCopyId={(id) => vm.copyBotId(id)}
      />
    </>
  );
}
