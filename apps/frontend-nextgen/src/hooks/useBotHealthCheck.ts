import type { BotHealthCheckSummary, BotHealthCheckTarget, BotHealthHistoryItem } from '@/domain/botHealthCheck';
import { botHealthCheckService } from '@/services/botHealthCheck';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';

export function useBotHealthCheck() {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<BotHealthCheckTarget | undefined>();
  const [summary, setSummary] = useState<BotHealthCheckSummary | undefined>();
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | undefined>();

  const load = useCallback(
    async (nextTarget: BotHealthCheckTarget = target as BotHealthCheckTarget) => {
      if (!nextTarget) return;
      setLoading(true);
      setError(undefined);
      try {
        const result = await botHealthCheckService.load(nextTarget);
        setSummary(result);
      } catch (err) {
        const message = err instanceof Error ? err.message : '健康检查结果加载失败';
        setError(message);
        toast.error(message);
      } finally {
        setLoading(false);
      }
    },
    [target],
  );

  const openHealthCheck = useCallback(
    (nextTarget: BotHealthCheckTarget) => {
      setTarget(nextTarget);
      setSummary(undefined);
      setError(undefined);
      setOpen(true);
      void load(nextTarget);
    },
    [load],
  );

  const closeHealthCheck = useCallback(() => {
    setOpen(false);
  }, []);

  const refresh = useCallback(async () => {
    await load();
  }, [load]);

  const viewHistoryItem = useCallback((item: BotHealthHistoryItem) => {
    toast.info(`${item.label} 详情暂未接入`);
  }, []);

  const runDiagnose = useCallback(async () => {
    if (!target) return;
    setChecking(true);
    setError(undefined);
    try {
      const result = await botHealthCheckService.runDiagnose(target);
      toast.success(result.scan_id ? `健康检查已开始（#${result.scan_id}）` : '健康检查已开始');
      await load(target);
    } catch (err) {
      const message = err instanceof Error ? err.message : '健康检查启动失败';
      setError(message);
      toast.error(message);
    } finally {
      setChecking(false);
    }
  }, [load, target]);

  return {
    open,
    target,
    summary,
    loading,
    checking,
    error,
    capability: botHealthCheckService.getCapability(),
    openHealthCheck,
    closeHealthCheck,
    refresh,
    runDiagnose,
    viewHistoryItem,
  };
}
