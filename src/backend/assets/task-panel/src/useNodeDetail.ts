/**
 * useNodeDetail — fetch TaskNodeDetailView on select; poll every 3s while the
 * node is active (running/hung/pending) and the task is non-terminal.
 * Mirrors StateMachineRunView node-detail poll (3121-3150).
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchNodeDetail } from './api';
import { DEFAULT_POLLING_INTERVAL, NODE_STATUS_TERMINAL, TASK_STATUS_TERMINAL } from './constants';
import type { TaskNodeDetailView } from './types';

export interface UseNodeDetailResult {
  detail: TaskNodeDetailView | null;
  loading: boolean;
}

export function useNodeDetail(
  taskId: string | undefined,
  nodeId: string | undefined,
  taskStatus: string | undefined,
): UseNodeDetailResult {
  const [detail, setDetail] = useState<TaskNodeDetailView | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const abortRef = useRef<AbortController | null>(null);

  const fetchOnce = useCallback(async () => {
    if (!taskId || !nodeId) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const d = await fetchNodeDetail(taskId, nodeId, { signal: controller.signal });
      if (!controller.signal.aborted) setDetail(d);
    } catch {
      // swallowed; modal keeps last detail (4xx likely means node just rotated)
    }
  }, [taskId, nodeId]);

  useEffect(() => {
    setDetail(null);
    if (!taskId || !nodeId) return;
    setLoading(true);
    fetchOnce().finally(() => setLoading(false));
    return () => {
      abortRef.current?.abort();
    };
  }, [taskId, nodeId, fetchOnce]);

  // poll while node active + task non-terminal
  useEffect(() => {
    if (!taskId || !nodeId || !detail) return undefined;
    const nodeTerminal = NODE_STATUS_TERMINAL.has(String(detail.status || '').toLowerCase());
    const taskTerminal = taskStatus ? TASK_STATUS_TERMINAL.has(String(taskStatus)) : false;
    if (nodeTerminal || taskTerminal) return undefined;
    const timer = window.setTimeout(() => {
      fetchOnce();
    }, DEFAULT_POLLING_INTERVAL);
    return () => window.clearTimeout(timer);
  }, [taskId, nodeId, detail, taskStatus, fetchOnce]);

  return { detail, loading };
}
