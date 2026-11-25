import { useEffect, useRef, useState } from 'react';
import { listExperts } from '@/services/expertMarketService';
import type { ExpertPage, ExpertQuery, ExpertSummary } from '@/domain/expertMarket';

/** 独立查询状态可供发现页与创建选择器复用，不共享两者的筛选条件。 */
export function useExpertCatalog(query: ExpertQuery) {
  const [page, setPage] = useState<ExpertPage>({ total: 0, items: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const sequence = useRef(0);
  const { keyword, category, sort, page_num, page_size } = query;

  useEffect(() => {
    const current = ++sequence.current;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void listExperts({ keyword, category, sort, page_num, page_size }, controller.signal)
      .then((next) => {
        if (current === sequence.current) setPage(next);
      })
      .catch((reason: unknown) => {
        if (current === sequence.current && !controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : '专家目录加载失败');
        }
      })
      .finally(() => {
        if (current === sequence.current) setLoading(false);
      });
    return () => {
      sequence.current += 1;
      controller.abort();
    };
  }, [keyword, category, sort, page_num, page_size, revision]);

  return { page, loading, error, reload: () => setRevision((value) => value + 1) };
}

/** 滚动追加式目录查询：过滤/排序决定首屏（第 1 页），`loadMore` 按页追加后续内容。 */
export interface ExpertCatalogFeedQuery {
  keyword: string;
  category: string;
  sort: 'hot' | 'newest';
  page_size: number;
}

/** 滚动加载目录流：`hasMore` 由已载条数与 total 对比得出，消费方据此决定是否继续触发。 */
export interface ExpertCatalogFeed {
  items: ExpertSummary[];
  total: number;
  hasMore: boolean;
  loading: boolean;
  loadingMore: boolean;
  error: string;
  loadMoreError: string;
  loadMore: () => void;
  reload: () => void;
}

const EMPTY_ERROR = '';

/**
 * 专家市场滚动加载流（与 `useCollaborationSquare` 的追加语义对齐）：
 * - 过滤/排序任一变化 → 重置为第 1 页重新拉取，作废在途请求（序号守卫，迟到的旧响应不可落盘）；
 * - `loadMore` 追加下一页；追加失败只置 `loadMoreError`，已载内容保持原样可重试；
 * - 以 total 截停；服务端若返回空页（口径变化），就地把 total 收敛到已载条数，避免空转循环。
 */
export function useExpertCatalogFeed({ keyword, category, sort, page_size }: ExpertCatalogFeedQuery): ExpertCatalogFeed {
  // items/total/next 同置一态：next 为下一次要拉的页号（首屏后从 2 起步）。
  const [stream, setStream] = useState<{ items: ExpertSummary[]; total: number; next: number }>({
    items: [],
    total: 0,
    next: 1,
  });
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(EMPTY_ERROR);
  const [loadMoreError, setLoadMoreError] = useState(EMPTY_ERROR);
  const [revision, setRevision] = useState(0);
  const sequence = useRef(0);

  useEffect(() => {
    const current = ++sequence.current;
    const controller = new AbortController();
    setLoading(true);
    setError(EMPTY_ERROR);
    setLoadMoreError(EMPTY_ERROR);
    void listExperts({ keyword, category, sort, page_num: 1, page_size }, controller.signal)
      .then((first) => {
        if (current !== sequence.current) return;
        setStream({ items: first.items, total: first.total, next: 2 });
      })
      .catch((reason: unknown) => {
        if (current === sequence.current && !controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : '专家目录加载失败');
        }
      })
      .finally(() => {
        if (current === sequence.current) setLoading(false);
      });
    return () => {
      sequence.current += 1;
      controller.abort();
    };
  }, [keyword, category, sort, page_size, revision]);

  const loadMore = () => {
    const { items, total, next } = stream;
    if (loading || loadingMore || error || items.length >= total) return;
    const current = ++sequence.current;
    const controller = new AbortController();
    setLoadingMore(true);
    setLoadMoreError(EMPTY_ERROR);
    void listExperts({ keyword, category, sort, page_num: next, page_size }, controller.signal)
      .then((more) => {
        if (current !== sequence.current) return;
        setStream((prev) => {
          const merged = prev.items.concat(more.items);
          const stalled = merged.length === prev.items.length && merged.length < more.total;
          return { items: merged, total: stalled ? merged.length : more.total, next: prev.next + 1 };
        });
      })
      .catch((reason: unknown) => {
        if (current === sequence.current && !controller.signal.aborted) {
          // 追加失败只报错不重置已载内容；重试继续从断点页拉。
          setLoadMoreError(reason instanceof Error ? reason.message : '加载更多专家失败');
        }
      })
      .finally(() => {
        if (current === sequence.current) setLoadingMore(false);
      });
  };

  return {
    items: stream.items,
    total: stream.total,
    hasMore: stream.items.length < stream.total,
    loading,
    loadingMore,
    error,
    loadMoreError,
    loadMore,
    reload: () => setRevision((value) => value + 1),
  };
}
