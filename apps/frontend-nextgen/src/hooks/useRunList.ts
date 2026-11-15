/**
 * Observes the run list by polling the gateway's summary endpoint.
 *
 * Same discipline as `useRunPolling`: the list is a projection of what the
 * gateway returned, never a local cache that can drift, and polling stops once
 * every visible run is terminal.
 */
import { useCallback, useEffect, useState } from 'react';
import { AgentswarmRequestError } from '@/services/agentswarm/http';
import { listRuns } from '@/services/agentswarm/service';
import { isTerminalStatus, type RunSummaryDTO } from '@/services/agentswarm/types';
import { POLL_INTERVAL_MS } from './useRunPolling';

export type ListPhase = 'idle' | 'polling' | 'stopped';

export interface UseRunListOptions {
  tenant: string | undefined;
  enabled?: boolean;
  page?: number;
  pageSize?: number;
  intervalMs?: number;
}

export interface UseRunListResult {
  runs: RunSummaryDTO[];
  total: number;
  loaded: boolean;
  phase: ListPhase;
  error: string | null;
  degraded: boolean;
  refresh: () => void;
}

export function useRunList({
  tenant,
  enabled = true,
  page = 1,
  pageSize = 20,
  intervalMs = POLL_INTERVAL_MS,
}: UseRunListOptions): UseRunListResult {
  const [runs, setRuns] = useState<RunSummaryDTO[]>([]);
  const [total, setTotal] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [phase, setPhase] = useState<ListPhase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [degraded, setDegraded] = useState(false);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((current) => current + 1), []);

  useEffect(() => {
    if (!tenant || !enabled) {
      setPhase('idle');
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    setPhase('polling');
    setError(null);

    const tick = async () => {
      try {
        const result = await listRuns(tenant, { page, page_size: pageSize });
        if (cancelled) return;
        setRuns(result.items ?? []);
        setTotal(result.total ?? 0);
        setLoaded(true);
        setDegraded(false);
        // Nothing left to observe: every visible run is finished.
        if ((result.items ?? []).every((item) => isTerminalStatus(item.status))) {
          setPhase('stopped');
          return;
        }
      } catch (reason) {
        if (cancelled) return;
        if (reason instanceof AgentswarmRequestError && reason.kind !== 'unconfirmed') {
          setError(reason.message);
          setPhase('stopped');
          return;
        }
        setDegraded(true);
      }
      if (cancelled) return;
      timer = setTimeout(tick, intervalMs);
    };

    void tick();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [tenant, enabled, page, pageSize, intervalMs, nonce]);

  return { runs, total, loaded, phase, error, degraded, refresh };
}