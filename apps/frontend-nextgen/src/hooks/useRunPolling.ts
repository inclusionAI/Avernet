/**
 * Observes one Agent Compute run by polling the gateway's run summary.
 *
 * The summary carries state and final answer only; the per-agent detail (full
 * message history, attempt ledger, results) is read separately and on demand, so
 * it never rides a poll tick.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AgentswarmRequestError } from '@/services/agentswarm/http';
import { getRun } from '@/services/agentswarm/service';
import { isTerminalStatus, type RunSummaryDTO } from '@/services/agentswarm/types';

/** Poll cadence for an active run. */
export const POLL_INTERVAL_MS = 4000;

export type PollPhase = 'idle' | 'polling' | 'stopped';

export interface UseRunPollingOptions {
  tenant: string | undefined;
  runId: string | undefined;
  intervalMs?: number;
  /** Skip polling entirely (e.g. the observation view is closed). */
  enabled?: boolean;
}

export interface UseRunPollingResult {
  run: RunSummaryDTO | null;
  phase: PollPhase;
  /** Set only for failures that end observation, never for a transient blip. */
  error: string | null;
  /** True when the last attempt failed but observation continues. */
  degraded: boolean;
  refresh: () => void;
}

function isGone(reason: unknown): boolean {
  return reason instanceof AgentswarmRequestError && reason.kind === 'not_found';
}

function isFatal(reason: unknown): boolean {
  // A 4xx names a real problem with this request and will not fix itself.
  if (reason instanceof AgentswarmRequestError) return reason.kind === 'refused' || reason.kind === 'not_found';
  return false;
}

export function useRunPolling({
  tenant,
  runId,
  intervalMs = POLL_INTERVAL_MS,
  enabled = true,
}: UseRunPollingOptions): UseRunPollingResult {
  const [run, setRun] = useState<RunSummaryDTO | null>(null);
  const [phase, setPhase] = useState<PollPhase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [degraded, setDegraded] = useState(false);
  const [nonce, setNonce] = useState(0);
  // Guards against a late response from a previous run overwriting the current one.
  const activeRun = useRef<string | undefined>(runId);

  const refresh = useCallback(() => setNonce((current) => current + 1), []);

  useEffect(() => {
    activeRun.current = runId;
    if (!tenant || !runId || !enabled) {
      setPhase('idle');
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    setPhase('polling');
    setError(null);
    setDegraded(false);

    const tick = async () => {
      try {
        const next = await getRun(tenant, runId);
        if (cancelled || activeRun.current !== runId) return;
        setRun(next);
        setDegraded(false);
        // A terminal run will not change again: stop rather than idle-poll.
        if (isTerminalStatus(next.status)) {
          setPhase('stopped');
          return;
        }
      } catch (reason) {
        if (cancelled || activeRun.current !== runId) return;
        if (isGone(reason)) {
          setError('该运行已不存在，可能已被清理');
          setPhase('stopped');
          return;
        }
        if (isFatal(reason)) {
          setError(reason instanceof Error ? reason.message : '读取运行状态失败');
          setPhase('stopped');
          return;
        }
        // Transient: keep the last known state and try again on the next tick.
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
  }, [tenant, runId, enabled, intervalMs, nonce]);

  return { run, phase, error, degraded, refresh };
}