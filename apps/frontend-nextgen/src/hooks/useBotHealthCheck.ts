import type {
  BotHealthCheckSummary,
  BotHealthCheckTarget,
  BotHealthHistoryItem,
  BotHealthPatch,
  BotHealthPatchPreview,
} from '@/domain/botHealthCheck';
import { botHealthCheckService } from '@/services/botHealthCheck';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

/** 待确认的修复请求：补丁 + 预览接口渲染出的 diff。preview 在预览失败时为空，仅提示错误。 */
export interface BotHealthPatchFixRequest {
  patch: BotHealthPatch;
  preview: BotHealthPatchPreview | undefined;
}

// 对齐老前端 useHarness 的轮询节奏：3s 一次，最长 11 分钟
const POLL_INTERVAL = 3000;
const MAX_POLL_TIME = 11 * 60 * 1000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export function useBotHealthCheck() {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<BotHealthCheckTarget | undefined>();
  const [summary, setSummary] = useState<BotHealthCheckSummary | undefined>();
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [patchFix, setPatchFix] = useState<BotHealthPatchFixRequest | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const pollAbortRef = useRef<AbortController | null>(null);

  // 卸载取消轮询，防止泄漏与过期 setState
  useEffect(() => () => pollAbortRef.current?.abort(), []);

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

  /** 检测结束（含部分维度失败）时的提示，对齐老前端 pollDimReport 的完成态 toast。 */
  const notifyDiagnoseSettled = useCallback((result: BotHealthCheckSummary) => {
    const failedReasons = result.dimensions
      .filter((dimension) => ['failed', 'error'].includes((dimension.scanStatus ?? '').toLowerCase()))
      .map((dimension) => dimension.failedReason)
      .filter((reason): reason is string => Boolean(reason));
    if (failedReasons.length > 0) {
      toast.error(failedReasons.join('；') || '部分维度检测失败');
    } else {
      toast.success('健康检查完成');
    }
  }, []);

  /**
   * 轮询体检报告直到检测结束。后端在 diagnose 落库时即写入 status='scanning' 的行，
   * 因此每轮都以整体状态判定：仍在 scanning 则继续，完成/失败则停止并提示。
   */
  const pollUntilSettled = useCallback(
    async (nextTarget: BotHealthCheckTarget) => {
      const controller = new AbortController();
      pollAbortRef.current?.abort();
      pollAbortRef.current = controller;
      setChecking(true);
      setError(undefined);
      try {
        const startTime = Date.now();
        while (!controller.signal.aborted && Date.now() - startTime < MAX_POLL_TIME) {
          try {
            const result = await botHealthCheckService.load(nextTarget);
            if (controller.signal.aborted) return;
            setSummary(result);
            if (result.overallStatus !== 'scanning') {
              notifyDiagnoseSettled(result);
              return;
            }
          } catch (err) {
            if (controller.signal.aborted) return;
            const message = err instanceof Error ? err.message : '健康检查结果轮询失败';
            setError(message);
            toast.error(message);
            return;
          }
          await sleep(POLL_INTERVAL);
        }
      } finally {
        if (pollAbortRef.current === controller) {
          pollAbortRef.current = null;
        }
        setChecking(false);
      }
    },
    [notifyDiagnoseSettled],
  );

  const openHealthCheck = useCallback(
    (nextTarget: BotHealthCheckTarget) => {
      setTarget(nextTarget);
      setSummary(undefined);
      setError(undefined);
      setOpen(true);
      // 若进入页面时后端仍在检测（报告呈 scanning），轮询会自动跟进到出结果
      void pollUntilSettled(nextTarget);
    },
    [pollUntilSettled],
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
    try {
      const result = await botHealthCheckService.runDiagnose(target);
      toast.success(result.scan_id ? `健康检查已开始（#${result.scan_id}）` : '健康检查已开始');
    } catch (err) {
      const message = err instanceof Error ? err.message : '健康检查启动失败';
      setError(message);
      toast.error(message);
      return;
    }
    await pollUntilSettled(target);
  }, [pollUntilSettled, target]);

  /**
   * 一键修复入口：先拉取补丁预览 diff，再由用户在预览弹窗中确认应用
   * （对齐老前端 Harness 的 preview → confirm → apply 流程）。
   */
  const openPatchFix = useCallback(
    async (patch: BotHealthPatch) => {
      if (!target) return;
      setPreviewLoading(true);
      setPatchFix({ patch, preview: undefined });
      try {
        const preview = await botHealthCheckService.previewPatch(target, patch.patch_id);
        setPatchFix((current) => (current?.patch.patch_id === patch.patch_id ? { ...current, preview } : current));
      } catch (err) {
        const message = err instanceof Error ? err.message : '补丁预览失败';
        toast.error(message);
        setPatchFix(null);
      } finally {
        setPreviewLoading(false);
      }
    },
    [target],
  );

  const closePatchFix = useCallback(() => {
    setPatchFix(null);
  }, []);

  /** 确认应用当前预览中的补丁，成功后刷新体检报告。 */
  const confirmPatchFix = useCallback(async () => {
    const patch = patchFix?.patch;
    if (!target || !patch) return false;
    setApplying(true);
    try {
      const result = await botHealthCheckService.applyPatch(target, patch.patch_id);
      if (!result.success) {
        toast.error(result.err_msg || `「${patch.name}」修复失败`);
        return false;
      }
      toast.success(`「${patch.name}」修复完成`);
      setPatchFix(null);
      await load(target);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : `「${patch.name}」修复失败`;
      toast.error(message);
      return false;
    } finally {
      setApplying(false);
    }
  }, [load, patchFix, target]);

  return {
    open,
    target,
    summary,
    loading,
    checking,
    error,
    capability: botHealthCheckService.getCapability(),
    patchFix,
    previewLoading,
    applying,
    openHealthCheck,
    closeHealthCheck,
    refresh,
    runDiagnose,
    viewHistoryItem,
    openPatchFix,
    closePatchFix,
    confirmPatchFix,
  };
}
