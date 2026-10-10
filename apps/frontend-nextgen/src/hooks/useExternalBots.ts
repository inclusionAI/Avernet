import { externalBotService, type ExternalBotDomain } from '@/services/botWorkshop/externalBotService';
import { useCallback, useEffect, useState } from 'react';

export function useExternalBots(enabled: boolean) {
  const [items, setItems] = useState<ExternalBotDomain[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(undefined);
    try {
      setItems(await externalBotService.list({ signal }));
    } catch (loadError) {
      if (signal?.aborted) return;
      setItems([]);
      setError(loadError instanceof Error ? loadError.message : '外部 Bot 列表加载失败');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [enabled, load]);

  return { items, loading, error, retry: () => load() };
}
