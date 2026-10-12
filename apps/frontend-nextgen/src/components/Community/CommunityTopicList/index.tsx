import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { CommunityTopic } from '@/domain/community/types';
import { ChevronDown, RefreshCw } from 'lucide-react';
import type { KeyboardEvent, RefObject } from 'react';
import { useEffect, useRef } from 'react';
import { CommunityAuthorAvatar } from '../CommunityAuthorAvatar';

// 滚到距底该距离时预取下一页（用户已滚动后，预取可减少触底等待）。
const LOAD_MORE_PRELOAD_DISTANCE = 320;

/** 相对时间（分钟前 / 小时前 / 天前 …），对齐原型。 */
function formatRelative(value: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) return '刚刚';
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return '刚刚';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} 天前`;
  const month = Math.floor(day / 30);
  return `${Math.floor(month / 12)} 年前`;
}

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
  /** 列表滚动容器（main），监听其 scroll 事件；由 CommunityPage 注入。 */
  scrollRootRef: RefObject<HTMLElement | null>;
}

function TopicRow({ topic, onOpen }: { topic: CommunityTopic; onOpen: (topic: CommunityTopic) => void }) {
  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    onOpen(topic);
  };

  // 列表行对齐原型 4 列：主题(1fr) / 发帖人(140px) / 回复数(64px) / 最新回复(176px)。
  const replyCount = typeof topic.replyCount === 'number' ? topic.replyCount : 0;
  const hasReplies = replyCount > 0;
  const activityAt = hasReplies ? topic.latestActivityAt ?? topic.createdAt : topic.createdAt;
  const activityPrefix = hasReplies ? '最新回复：' : '发帖时间：';

  return (
    <article
      role="button"
      tabIndex={0}
      onClick={() => onOpen(topic)}
      onKeyDown={handleKeyDown}
      className="grid grid-cols-[1fr_140px_64px_176px] items-center gap-3 border-b border-border/60 px-4 py-3 outline-none transition-colors last:border-b-0 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <h3 className="m-0 min-w-0 truncate text-sm font-semibold text-foreground">{topic.title}</h3>
          {topic.status === 'closed' && <Badge tone="neutral">已结帖</Badge>}
        </div>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">{topic.body}</p>
      </div>
      <div className="flex min-w-0 items-center gap-2">
        <CommunityAuthorAvatar author={topic.author} size={24} />
        <span className="truncate text-xs text-muted-foreground">{topic.author.displayName}</span>
      </div>
      <div className="text-right text-xs font-medium text-muted-foreground tabular-nums">{replyCount} 回复</div>
      <div className="min-w-0 text-right">
        <span className="truncate text-xs text-muted-foreground tabular-nums">
          {activityPrefix}
          {formatRelative(activityAt)}
        </span>
      </div>
    </article>
  );
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
      <div className="overflow-hidden rounded-xl border border-border bg-card" aria-label="社区主题加载中">
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
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      {topics.map((topic) => (
        <TopicRow key={topic.id} topic={topic} onOpen={onOpen} />
      ))}
      {loadingMore && (
        <div aria-live="polite" className="py-4 text-center text-xs text-muted-foreground">
          正在加载更多...
        </div>
      )}
      {!loadingMore && loadMoreError && (
        <div className="flex items-center justify-between gap-3 border-t border-border p-4">
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
