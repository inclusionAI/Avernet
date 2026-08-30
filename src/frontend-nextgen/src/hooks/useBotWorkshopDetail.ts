import { botWorkshopService, type BotDomain } from '@/services/botWorkshop';
import { history } from '@umijs/max';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

export function useBotWorkshopDetail(id: string | null, editable: boolean, enabled = true) {
  const [bot, setBot] = useState<BotDomain>();
  const [loading, setLoading] = useState(Boolean(id));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const load = useCallback(async () => {
    if (!id || !enabled) return;
    setLoading(true);
    setError(undefined);
    try {
      setBot(await botWorkshopService.detail(id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Bot 详情加载失败');
    } finally {
      setLoading(false);
    }
  }, [enabled, id]);
  useEffect(() => {
    void load();
  }, [load]);
  const save = useCallback(
    async (values: { name: string; description: string }) => {
      if (!id || !editable) return;
      setSaving(true);
      try {
        const updated = await botWorkshopService.update(id, values);
        setBot(updated);
        toast.success('Bot 基础信息已保存');
      } catch (reason) {
        toast.error(reason instanceof Error ? reason.message : '保存失败');
        throw reason;
      } finally {
        setSaving(false);
      }
    },
    [editable, id],
  );
  return { bot, loading, saving, error, retry: load, save, back: () => history.push('/bot-workshop') };
}
