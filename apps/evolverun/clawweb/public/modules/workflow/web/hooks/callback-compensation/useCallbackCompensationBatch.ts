import {
  callbackCompensationBatchResolved,
  callbackCompensationStorageKey,
} from '../../models/callbackCompensation';
import {
  callbackCompensationErrorMessage,
  callbackCompensationErrorStatus,
} from '../../models/callbackCompensationErrors';
import type {
  CallbackCompensationBatch,
  CallbackCompensationScope,
} from '../../models/callbackCompensationTypes';
import { callbackCompensationService as service } from '../../api/callback-compensation';
import { useCallback, useEffect, useRef, useState } from 'react';

export function useCallbackCompensationBatch(
  scope: CallbackCompensationScope & { scopeKey: string; userId?: string },
  open: boolean,
  onDenied: () => void,
) {
  const storageKey = callbackCompensationStorageKey(scope);
  const [batchId, setBatchId] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(storageKey) || null;
    } catch {
      return null;
    }
  });
  const [batch, setBatch] = useState<CallbackCompensationBatch | null>(null);
  const batchRef = useRef<CallbackCompensationBatch | null>(null);
  const [busy, setBusy] = useState<'read' | 'stop' | null>(null);
  const [error, setError] = useState('');
  const [storageWarning, setStorageWarning] = useState('');
  const alive = useRef(true),
    request = useRef<AbortController | null>(null),
    sequence = useRef(0);
  const busyRef = useRef<'read' | 'stop' | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      sequence.current++;
      request.current?.abort();
    };
  }, []);
  const accept = useCallback(
    (value: CallbackCompensationBatch) => {
      if (!alive.current) return;
      batchRef.current = value;
      setBatch(value);
      setBatchId(value.batchId);
      setError('');
      try {
        sessionStorage.setItem(storageKey, value.batchId);
      } catch {
        setStorageWarning('本标签页无法保存批次 ID；关闭窗口仍可查看，但刷新页面后无法自动恢复。');
      }
    },
    [storageKey],
  );
  const load = useCallback(
    async (stop = false) => {
      if (!batchId || !alive.current || busyRef.current === 'stop' || (!stop && busyRef.current)) return;
      // A late poll must not overwrite a stop response. Only queued items can be stopped.
      if (
        stop &&
        (!batch ||
          !['queued', 'running'].includes(batch.status) ||
          !batch.items.some((item) => item.state === 'queued'))
      )
        return;
      request.current?.abort();
      const controller = new AbortController(),
        version = ++sequence.current;
      request.current = controller;
      busyRef.current = stop ? 'stop' : 'read';
      setBusy(busyRef.current);
      setError('');
      const valid = () => alive.current && !controller.signal.aborted && sequence.current === version;
      try {
        const value = await (stop ? service.stop : service.batch)(
          { workflowId: scope.workflowId, environment: scope.environment },
          batchId,
          controller.signal,
        );
        if (valid()) accept(value);
      } catch (failure) {
        if (!valid()) return;
        if ([401, 403].includes(Number(callbackCompensationErrorStatus(failure)))) onDenied();
        else
          setError(
            callbackCompensationErrorMessage(
              failure,
              stop
                ? '停止结果尚未确认，请刷新同一批次。已发送或结果未知的通知不会重发。'
                : '批次暂时无法读取，请重试同一批次；不会创建新批次。',
            ),
          );
      } finally {
        if (valid()) {
          busyRef.current = null;
          setBusy(null);
        }
      }
    },
    [batchId, batch, scope.workflowId, scope.environment, accept, onDenied],
  );
  useEffect(() => {
    if (!open || !batchId || busy) return;
    // An errored initial restoration requires an explicit retry, never a tight retry loop.
    if (!batch) {
      if (!error) void load();
      return;
    }
    if (callbackCompensationBatchResolved(batch)) return;
    const timer = window.setTimeout(() => void load(), 2500);
    return () => window.clearTimeout(timer);
  }, [open, batchId, batch, busy, error, load]);
  const resetResolved = () => {
    if (!alive.current || busyRef.current || error || !callbackCompensationBatchResolved(batchRef.current))
      return false;
    // Explicitly detach only this resolved batch. No server record, eligibility or create key is reset.
    try {
      sessionStorage.removeItem(storageKey);
    } catch {
      setStorageWarning('无法清除本标签页的批次 ID，请保持当前批次并重试；未开始新的补偿。');
      return false;
    }
    sequence.current++;
    request.current?.abort();
    batchRef.current = null;
    setBatch(null);
    setBatchId(null);
    setError('');
    setStorageWarning('');
    return true;
  };
  return {
    storageKey,
    batchId,
    batch,
    busy,
    error,
    storageWarning,
    accept,
    resetResolved,
    canStartNew: callbackCompensationBatchResolved(batch) && !busy && !error,
    refresh: () => void load(),
    stop: () => void load(true),
    canStop:
      !!batch && ['queued', 'running'].includes(batch.status) && batch.items.some((item) => item.state === 'queued'),
  };
}
