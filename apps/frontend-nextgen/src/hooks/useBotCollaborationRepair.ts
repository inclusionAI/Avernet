import { botCollaborationRepairService as service } from '@/services/bots/botCollaborationRepairService';
import type { CollaborationRepairReport } from '@/services/bots/botCollaborationRepairService';
import { safeReportError } from '@/utils/errorToast';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

/**
 * 一次性 Bot 协作信息修复 Hook：同一 (botId, userId) 的并发触发只发一次 POST，
 * loading 同时承担同步双击保护；重开后通过 service.pending 继续跟踪在途请求，不重发 POST。
 * 刷新动作由宿主注入，service 本身不 Toast。generation 使卸载、切 Bot、enabled 变化后
 * 的迟到结果既不改状态也不提示。
 */
export function useBotCollaborationRepair(
  botId: string,
  userId: string,
  enabled: boolean,
  refresh: () => Promise<void>,
) {
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<CollaborationRepairReport>();
  const [error, setError] = useState<string>();
  const [refreshError, setRefreshError] = useState<string>();
  const busy = useRef(false);
  const generation = useRef(0);
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const settle = useCallback(async (request: Promise<CollaborationRepairReport>, version: number) => {
    try {
      const result = await request;
      if (generation.current !== version) return;
      setReport(result);
      if (result.status === 'succeeded') toast.success('Bot 信息已修复');
      else if (result.status === 'partial_failed') toast.warning('部分 Bot 信息修复失败');
      else toast.error('Bot 信息修复失败');
      if (result.status !== 'failed') {
        try {
          await refreshRef.current();
        } catch {
          if (generation.current === version) setRefreshError('修复请求已完成，配置刷新失败，请重新打开查看');
        }
      }
    } catch (cause) {
      if (generation.current !== version) return;
      setError('未能确认修复结果，请稍后重试');
      safeReportError(cause, { title: 'Bot 信息修复失败' });
    } finally {
      if (generation.current === version) {
        busy.current = false;
        setLoading(false);
      }
    }
  }, []);
  useEffect(() => {
    const version = ++generation.current;
    busy.current = false;
    setLoading(false);
    setReport(undefined);
    setError(undefined);
    setRefreshError(undefined);
    const active = enabled ? service.pending(botId, userId) : undefined;
    if (active) {
      busy.current = true;
      setLoading(true);
      void settle(active, version);
    }
    return () => {
      generation.current += 1;
    };
  }, [botId, userId, enabled, settle]);
  const run = useCallback(async () => {
    if (!enabled || busy.current) return;
    busy.current = true;
    setLoading(true);
    setReport(undefined);
    setError(undefined);
    setRefreshError(undefined);
    await settle(service.repair(botId, userId), generation.current);
  }, [botId, userId, enabled, settle]);
  return { loading, report, error, refreshError, run };
}
