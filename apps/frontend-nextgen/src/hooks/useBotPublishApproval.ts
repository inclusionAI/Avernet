import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

export function useBotPublishApproval(botId: string, ownerId: string | undefined, enabled: boolean) {
  const [required, setRequired] = useState(false);
  const [loading, setLoading] = useState(enabled);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState<string>();

  const load = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    setError(undefined);
    try {
      setRequired(await botEditorService.loadApproval(botId, ownerId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '发布审批配置加载失败');
    } finally {
      setLoading(false);
    }
  }, [botId, enabled, ownerId]);

  useEffect(() => {
    void load();
  }, [load]);

  const update = useCallback(
    async (next: boolean) => {
      if (!enabled || updating) return;
      setUpdating(true);
      try {
        await botEditorService.saveApproval(botId, next, ownerId);
        setRequired(next);
        setError(undefined);
        toast.success('发布审批配置已更新');
      } catch (cause) {
        toast.error(cause instanceof Error ? cause.message : '发布审批配置更新失败');
      } finally {
        setUpdating(false);
      }
    },
    [botId, enabled, ownerId, updating],
  );

  return { required, loading, updating, error, update, reload: load };
}
