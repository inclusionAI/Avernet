import type { DashboardResource, DashboardTimeRange } from '@/domain/taskEscortWorkspace/dashboardTypes';
import { isDashboardForbidden } from '@/services/taskEscortWorkspace/dashboardService';
import { useCallback, useEffect, useMemo, useState } from 'react';

/** A resource owns its retry and stale guard; independent read errors cannot discard other widgets. */
export function useTaskEscortDashboardResource<T>(
  identity: object,
  enabled: boolean,
  range: DashboardTimeRange,
  revision: number,
  read: (range: DashboardTimeRange, signal?: AbortSignal) => Promise<T>,
  onForbidden: () => void,
): DashboardResource<T> {
  const [retryVersion, setRetryVersion] = useState(0);
  const retry = useCallback(() => setRetryVersion((value) => value + 1), []);
  const token = useMemo(() => ({}), [identity, enabled, range.from, range.to, revision, retryVersion, read]);
  const [state, setState] = useState<{ token: object; data: T | null; loading: boolean; error: string | null } | null>(
    null,
  );
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    let active = true;
    setState({ token, data: null, loading: true, error: null });
    void read({ from: range.from, to: range.to }, abort.signal).then(
      (data) => {
        if (active) setState({ token, data, loading: false, error: null });
      },
      (error: unknown) => {
        if (!active) return;
        if (isDashboardForbidden(error)) onForbidden();
        setState({
          token,
          data: null,
          loading: false,
          error: error instanceof Error ? error.message : '大盘数据读取失败，请重试',
        });
      },
    );
    return () => {
      active = false;
      abort.abort();
    };
  }, [token, enabled, onForbidden]);
  // Hide data during the render that changes scope/range, before effect cleanup runs.
  if (!enabled || state?.token !== token) return { data: null, loading: enabled, error: null, retry };
  return { data: state.data, loading: state.loading, error: state.error, retry };
}
