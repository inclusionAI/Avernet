import { getExpert } from '@/services/expertMarketService';
import type { ExpertDetail } from '@/domain/expertMarket';
import { useEffect, useState } from 'react';

export function useExpertDetails(expertId: string) {
  const [expert, setExpert] = useState<ExpertDetail | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setExpert(null);
    void getExpert(expertId, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setExpert(value); })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '详情加载失败');
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [expertId, revision]);
  return { expert, error, loading, reload: () => setRevision((value) => value + 1) };
}
