import { listExpertCategories } from '@/services/expertMarketService';
import { useEffect, useState } from 'react';

export function useExpertCategories() {
  const [categories, setCategories] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    void listExpertCategories(controller.signal)
      .then((value) => { if (!controller.signal.aborted) setCategories(value); })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '分类加载失败');
      });
    return () => controller.abort();
  }, [revision]);
  return { categories, error, reload: () => setRevision((value) => value + 1) };
}
