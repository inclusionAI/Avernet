import { useEffect, useRef, useState } from 'react';
import { listExperts } from '@/services/expertMarketService';
import type { ExpertPage, ExpertQuery } from '@/domain/expertMarket';

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
