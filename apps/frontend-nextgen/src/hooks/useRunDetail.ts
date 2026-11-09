/**
 * Reads a run's per-agent detail on demand.
 *
 * The detail payload carries every agent's full message history, attempt ledger
 * and result, and grows with the run. It is therefore read once when the user
 * opens the per-agent view or once when the run reaches terminal — never on a
 * poll tick.
 */
import { useCallback, useEffect, useState } from 'react';
import { AgentswarmRequestError } from '@/services/agentswarm/http';
import { getRunDetail } from '@/services/agentswarm/service';
import type { RunDetailDTO } from '@/services/agentswarm/types';

export type DetailPhase = 'idle' | 'loading' | 'ready' | 'error';

export interface UseRunDetailOptions {
  tenant: string | undefined;
  runId: string | undefined;
  /** Set when the user opens the per-agent view. */
  opened: boolean;
  /** Set when the observed run is terminal; triggers the single terminal read. */
  terminal: boolean;
}

export interface UseRunDetailResult {
  detail: RunDetailDTO | null;
  phase: DetailPhase;
  error: string | null;
  reload: () => void;
}

export function useRunDetail({ tenant, runId, opened, terminal }: UseRunDetailOptions): UseRunDetailResult {
  const [detail, setDetail] = useState<RunDetailDTO | null>(null);
  const [phase, setPhase] = useState<DetailPhase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((current) => current + 1), []);

  // One read per run per trigger: opening the view, or the run finishing.
  useEffect(() => {
    if (!tenant || !runId || !(opened || terminal)) {
      if (!runId) {
        setDetail(null);
        setPhase('idle');
      }
      return;
    }
    let cancelled = false;
    setPhase('loading');
    setError(null);
    void getRunDetail(tenant, runId)
      .then((result) => {
        if (cancelled) return;
        setDetail(result);
        setPhase('ready');
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        const message =
          reason instanceof AgentswarmRequestError && reason.kind === 'not_found'
            ? '该运行已不存在，可能已被清理'
            : reason instanceof Error
              ? reason.message
              : '读取运行详情失败';
        setError(message);
        setPhase('error');
      });
    return () => {
      cancelled = true;
    };
    // `opened`/`terminal` are triggers, not values to re-read on every poll tick:
    // once the detail is loaded, a further terminal flip must not refetch it.
  }, [tenant, runId, opened || terminal, nonce]);

  return { detail, phase, error, reload };
}