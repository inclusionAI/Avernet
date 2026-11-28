import { dashboardRange } from '@/domain/taskEscortWorkspace/dashboard';
import { probeDashboardAccess } from '@/services/taskEscortWorkspace/dashboardService';
import { useEffect, useState } from 'react';

export type TaskEscortDashboardAccess = 'unknown' | 'allowed' | 'denied';

interface DashboardAccessScope {
  key: string;
  ready: boolean;
  loading?: boolean;
  error?: string;
}

/**
 * 以一次真实只读探针判定「数据大盘」可见性：403 是普通身份的预期结果，不弹错误。
 * 探针是唯一可靠的客户端管理员信号；未决或网络失败时保持 unknown（不错误隐藏管理员入口）。
 */
export function useTaskEscortDashboardAccess(scope: DashboardAccessScope): TaskEscortDashboardAccess {
  const [result, setResult] = useState<{ key: string; access: TaskEscortDashboardAccess }>({
    key: scope.key,
    access: 'unknown',
  });
  useEffect(() => {
    if (!scope.ready || scope.loading || scope.error) return;
    const controller = new AbortController();
    let active = true;
    const range = dashboardRange('today');
    void (async () => {
      try {
        const allowed = await probeDashboardAccess({ from: range.from, to: range.to }, controller.signal);
        if (active) setResult({ key: scope.key, access: allowed ? 'allowed' : 'denied' });
      } catch {
        if (active) setResult({ key: scope.key, access: 'unknown' });
      }
    })();
    return () => {
      active = false;
      controller.abort();
    };
  }, [scope.key, scope.ready, scope.loading, scope.error]);
  return result.key === scope.key ? result.access : 'unknown';
}
