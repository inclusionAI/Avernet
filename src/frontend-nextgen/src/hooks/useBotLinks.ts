import type { BotLink, BotLinkInput } from '@/domain/botLinks';
import { botLinkService } from '@/services/botWorkshop/botLinkService';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
export function useBotLinks(botId: string) {
  const [items, setItems] = useState<BotLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    void botLinkService
      .list(botId)
      .then((rows) => {
        if (active) setItems(rows);
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : '链接加载失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [botId, revision]);
  const act = useCallback(async (work: () => Promise<unknown>) => {
    try {
      await work();
      toast.success('关联链接已更新');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '关联链接操作失败');
      throw e;
    } finally {
      // A sync failure can follow persistence; always reload the authoritative state.
      setRevision((n) => n + 1);
    }
  }, []);
  return {
    items,
    loading,
    error,
    refresh: () => setRevision((n) => n + 1),
    add: (links: BotLinkInput[]) => act(() => botLinkService.add(botId, links)),
    update: (id: string, input: Partial<BotLinkInput>) => act(() => botLinkService.update(botId, id, input)),
    remove: (id: string) => act(() => botLinkService.remove(botId, id)),
  };
}
