/**
 * useTaskGraph — polling hook for TaskGraphView.
 * Ported from bcsPanel.StateMachineRunView polling (2999-3021) + transient
 * backoff (isRetryableRequestStatus:1995). Polls GET /api/tasks/{id}/graph
 * every `pollingInterval` (default 3s) while root_phase is non-terminal
 * (drafting/defined/executing/reviewing); stops on terminal (done/cancelled/
 * failed). 5xx/network errors back off up to MAX_TRANSIENT_RETRIES; 4xx is a
 * fatal error (stops).
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchTaskGraph, isRetryableStatus } from './api';
import {
  DEFAULT_POLLING_INTERVAL,
  MAX_BACKOFF,
  MAX_TRANSIENT_RETRIES,
  ROOT_PHASE_TERMINAL,
} from './constants';
import type { TaskGraphView } from './types';

export interface UseTaskGraphResult {
  graph: TaskGraphView | null;
  loading: boolean;
  error: Error | null;
  refresh: () => void;
}

export function useTaskGraph(
  taskId: string | undefined,
  options: { autoRefresh?: boolean; pollingInterval?: number } = {},
): UseTaskGraphResult {
  const { autoRefresh = true, pollingInterval = DEFAULT_POLLING_INTERVAL } = options;
  const [graph, setGraph] = useState<TaskGraphView | null>(null);
  const [loading, setLoading] = useState<boolean>(!!taskId);
  const [error, setError] = useState<Error | null>(null);
  const [refreshSignal, setRefreshSignal] = useState<number>(0);

  const abortRef = useRef<AbortController | null>(null);
  const transientRetryRef = useRef<number>(0);
  const graphRef = useRef<TaskGraphView | null>(null);
  graphRef.current = graph;

  const fetchOnce = useCallback(
    async (mode: 'initial' | 'refresh') => {
      if (!taskId) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const snap = await fetchTaskGraph(taskId, { signal: controller.signal });
        transientRetryRef.current = 0;
        setGraph(snap);
        if (mode === 'initial') setLoading(false);
        setError(null);
      } catch (err: unknown) {
        if (controller.signal.aborted) return;
        const e = err as Error & { status?: number };
        if (isRetryableStatus(e?.status)) {
          // transient — keep previous graph, back off, retry
          transientRetryRef.current += 1;
          if (transientRetryRef.current > MAX_TRANSIENT_RETRIES) {
            setError(e);
            if (mode === 'initial') setLoading(false);
          }
        } else {
          // fatal (4xx) — stop
          setError(e);
          if (mode === 'initial') setLoading(false);
        }
      }
    },
    [taskId],
  );

  // initial / manual refresh
  useEffect(() => {
    if (!taskId) {
      setGraph(null);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    fetchOnce('initial');
    return () => {
      abortRef.current?.abort();
    };
  }, [taskId, fetchOnce, refreshSignal]);

  // polling effect (ported from StateMachineRunView:2999)
  useEffect(() => {
    if (!autoRefresh || !taskId) return undefined;
    const current = graphRef.current;
    const rootPhase = current?.root_phase;
    const isTerminal = rootPhase ? ROOT_PHASE_TERMINAL.has(String(rootPhase)) : false;
    const shouldRetryTransient = !current && transientRetryRef.current > 0;
    if (isTerminal && !shouldRetryTransient) return undefined;

    const retryCount = transientRetryRef.current;
    const delay =
      retryCount > 0
        ? Math.min(pollingInterval * Math.pow(2, retryCount - 1), MAX_BACKOFF)
        : pollingInterval;

    const timer = window.setTimeout(() => {
      fetchOnce(current ? 'refresh' : 'initial');
    }, delay);
    return () => window.clearTimeout(timer);
  }, [autoRefresh, taskId, pollingInterval, fetchOnce, graph, refreshSignal]);

  const refresh = useCallback(() => {
    setRefreshSignal((n) => n + 1);
  }, []);

  return { graph, loading, error, refresh };
}
