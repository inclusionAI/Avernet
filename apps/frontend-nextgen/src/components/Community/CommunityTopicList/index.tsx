import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { CommunityTopic, InteractionSummary } from '@/domain/community/types';
import { ChevronDown, RefreshCw } from 'lucide-react';
import type { RefObject } from 'react';
import { useEffect, useRef } from 'react';
import { TopicRow } from './TopicRow';
// 滚到距底该距离时预取下一页（用户已滚动后，预取可减少触底等待）。
const LOAD_MORE_PRELOAD_DISTANCE = 320;

export interface CommunityTopicListProps {
  topics: CommunityTopic[];
  loading: boolean;
  error: string | null;
  hasFilter: boolean;
  onOpen: (topic: CommunityTopic) => void;
  onRetry: () => void;
  onClearFilter: () => void;
  onPublish: () => void;
  /** 无限滚动：是否还有更多可加载。 */
  hasMore: boolean;
  /** 无限滚动：正在加载下一页。 */
  loadingMore: boolean;
  /** 无限滚动：加载更多失败信息（可重试）。 */
  loadMoreError: string | null;
  /** 无限滚动：触发加载下一页。 */
  loadMore: () => void;
  /** 获取主题的互动总量；仅在命中列表批量 summary 后覆盖旧 likeCount。 */
  getTopicSummary?: (topic: CommunityTopic) => InteractionSummary | undefined;
  /** 列表滚动容器（main），监听其 scroll 事件；由 CommunityPage 注入。 */
  scrollRootRef: RefObject<HTMLElement | null>;
}

export function CommunityTopicList({
  topics,
  loading,
  error,
  hasFilter,
  onOpen,
  onRetry,
  onClearFilter,
  onPublish,
  hasMore,
  loadingMore,
  loadMoreError,
  loadMore,
  getTopicSummary,
  scrollRootRef,
}: CommunityTopicListProps) {
  // 同步防抖：scroll 事件密集触发时，只放过一次 loadMore（直到本次加载结束）。
  const loadMoreInFlightRef = useRef(false);
  const canLoadMore = hasMore && !loading && !loadingMore && !error && !loadMoreError;

  // 加载结束后解除 in-flight 锁，允许下次滚动继续加载。
  useEffect(() => {
    if (!loadingMore) loadMoreInFlightRef.current = false;
  }, [loadingMore]);

  // 下拉加载：两路监听滚动容器 ——
  // ① scroll：仅当用户已真实滚动（scrollTop>0，排除进页面静止态）且接近底部时触发下一页。
  //    进页面未滚动时不触发，避免「还没下拉就静默刷出全部」。
  // ② wheel：当列表内容不足以撑满滚动容器（scrollHeight<=clientHeight，容器自身不滚动）时，
  //    纯 scroll 事件永不触发，用户「向下滚」意图无法被捕获 —— 额外监听 wheel：向下滚且
  //    不可滚动 / 已接近底部时触发下一页（仅随用户真实滚轮操作，不会引起「进页面连刷」）。
  useEffect(() => {
    const root = scrollRootRef.current;
    if (!root || !canLoadMore || typeof root.addEventListener !== 'function') return;
    const onScroll = () => {
      if (loadMoreInFlightRef.current) return;
      if (root.scrollTop <= 0) return; // 静止/滚回顶部：不自动加载
      const remaining = root.scrollHeight - root.scrollTop - root.clientHeight;
      if (remaining <= LOAD_MORE_PRELOAD_DISTANCE) {
        loadMoreInFlightRef.current = true;
        void loadMore();
      }
    };
    const onWheel = (event: WheelEvent) => {
      if (loadMoreInFlightRef.current) return;
      if (event.deltaY <= 0) return; // 仅响应向下滚
      const notScrollable = root.scrollHeight <= root.clientHeight;
      const remaining = root.scrollHeight - root.scrollTop - root.clientHeight;
      if (!notScrollable && remaining > LOAD_MORE_PRELOAD_DISTANCE) return;
      loadMoreInFlightRef.current = true;
      void loadMore();
    };
    root.addEventListener('scroll', onScroll, { passive: true });
    root.addEventListener('wheel', onWheel, { passive: true });
    return () => {
      root.removeEventListener('scroll', onScroll);
      root.removeEventListener('wheel', onWheel);
    };
  }, [canLoadMore, loadMore, scrollRootRef]);

  if (loading) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-card" aria-label="社区主题加载中">
        {[1, 2, 3].map((item) => (
          <Skeleton.ListItem key={item} />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-xl border border-border bg-card">
        <Empty
          title="社区加载失败"
          description={error}
          action={
            <Button variant="outline" leftIcon={<RefreshCw className="size-4" />} onClick={onRetry}>
              重试
            </Button>
          }
        />
      </div>
    );
  }
  if (!topics.length) {
    return (
      <div className="rounded-xl border border-border bg-card">
        <Empty
          title={hasFilter ? '没有找到匹配主题' : '还没有主题'}
          description={hasFilter ? '试试清空搜索或切换到全部主题。' : '发布第一个主题，开始社区讨论。'}
          action={
            <Button variant={hasFilter ? 'outline' : 'default'} onClick={hasFilter ? onClearFilter : onPublish}>
              {hasFilter ? '清空筛选' : '发布主题'}
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      {topics.map((topic) => (
        <TopicRow
          key={topic.id}
          topic={topic}
          getTopicSummary={getTopicSummary}
          onOpen={onOpen}
        />
      ))}
      {loadingMore && (
        <div aria-live="polite" className="py-4 text-center text-xs text-muted-foreground">
          正在加载更多...
        </div>
      )}
      {!loadingMore && loadMoreError && (
        <div className="flex items-center justify-between gap-3 border-t border-divider p-4">
          <p className="m-0 text-sm text-muted-foreground">{loadMoreError}</p>
          <Button variant="secondary" size="sm" onClick={() => void loadMore()}>
            重试
          </Button>
        </div>
      )}
      {/* 显式「加载更多」：可滚动时是备选触发（也可滚到底自动加载），不可滚动时是唯一触发。 */}
      {hasMore && !loadingMore && !loadMoreError && (
        <div className="flex justify-center p-4">
          <Button
            variant="outline"
            size="sm"
            leftIcon={<ChevronDown className="size-4" aria-hidden />}
            // hover/focus 预取：滚动路径距底 320px 已提前发请求；显式按钮在鼠标/键盘进入时
            // 同步启动，减少真正点击时的等待感。重复触发由 useCommunity.loadMore 的 in-flight 守卫挡住。
            onMouseEnter={() => void loadMore()}
            onFocus={() => void loadMore()}
            onClick={() => void loadMore()}
          >
            加载更多
          </Button>
        </div>
      )}
      {!hasMore && !loadingMore && !loadMoreError && (
        <div className="py-4 text-center text-xs text-muted-foreground">没有更多主题了</div>
      )}
    </div>
  );
}
