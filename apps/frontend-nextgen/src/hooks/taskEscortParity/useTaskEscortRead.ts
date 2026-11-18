import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useCallback, useEffect, useMemo, useState } from 'react';

/** Caller supplies a stable useCallback. Render-time tokens prevent even one frame of stale evidence. */
export function useTaskEscortRead<T>(
  context: TaskEscortParityContext,
  key: string,
  read: (signal: AbortSignal) => Promise<T>,
): { data: T | null; loading: boolean; error: string | null; refresh: () => void } {
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((current) => current + 1), []);
  const token = useMemo(
    () => ({}),
    [context.scopeKey, context.userId, context.workflowId, context.ready, key, read, revision],
  );
  const [state, setState] = useState<{
    token: object;
    data: T | null;
    loading: boolean;
    error: string | null;
  } | null>(null);

  useEffect(() => {
    if (!context.ready) return;
    const controller = new AbortController();
    let active = true;
    setState({ token, data: null, loading: true, error: null });
    // The async boundary also converts a synchronous reader throw into a real error state.
    void (async () => read(controller.signal))().then(
      (data) => {
        if (active && !controller.signal.aborted) setState({ token, data, loading: false, error: null });
      },
      (error: unknown) => {
        if (active && !controller.signal.aborted)
          setState({
            token,
            data: null,
            loading: false,
            error: error instanceof Error ? error.message : '数据读取失败，请重试',
          });
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [token, context.ready]);

  if (!context.ready || state?.token !== token) return { data: null, loading: context.ready, error: null, refresh };
  return { data: state.data, loading: state.loading, error: state.error, refresh };
}
