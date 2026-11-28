import {
  callbackCompensationCanExecute,
  callbackCompensationDefaultRange,
  callbackCompensationExecutionReady,
  callbackCompensationRange,
  callbackCompensationStorageKey,
} from '@/domain/taskEscortParity/callbackCompensation';
import {
  callbackCompensationErrorMessage,
  callbackCompensationErrorStatus,
} from '@/domain/taskEscortParity/callbackCompensationErrors';
import type {
  CallbackCompensationCapabilities,
  CallbackCompensationCreate,
  CallbackCompensationPreview,
  CallbackCompensationRange,
} from '@/domain/taskEscortParity/callbackCompensationTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { callbackCompensationService as service } from '@/services/taskEscortParity/callbackCompensationService';
import { useEffect, useRef, useState } from 'react';
import { useCallbackCompensationBatch } from './useCallbackCompensationBatch';

// In-memory only: keep an ambiguous create frozen across route remounts. Never persist reasons/payloads/keys.
// Partitioning is for UI isolation, not authorization; every retry still goes through server auth.
const unresolvedCreates = new Map<string, CallbackCompensationCreate>();
function requestKey() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  return Array.from(
    bytes,
    (value, index) => `${[4, 6, 8, 10].includes(index) ? '-' : ''}${value.toString(16).padStart(2, '0')}`,
  ).join('');
}
export function useCallbackCompensation(
  context: TaskEscortParityContext,
  capabilities: CallbackCompensationCapabilities,
  onDenied: () => void,
) {
  const scope = { workflowId: context.workflowId, environment: capabilities.environment };
  const key = callbackCompensationStorageKey({ ...scope, scopeKey: context.scopeKey, userId: context.userId });
  const [open, setOpen] = useState(false);
  const [range, setRange] = useState(callbackCompensationDefaultRange);
  const [configId, setConfigId] = useState(capabilities.configs[0]?.configId ?? '');
  const [runIds, setRunIds] = useState('');
  const [mode, setMode] = useState<'time' | 'ids'>('time');
  const [preview, setPreview] = useState<CallbackCompensationPreview | null>(null);
  const [previewRange, setPreviewRange] = useState<CallbackCompensationRange | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState(''),
    [acknowledged, setAcknowledged] = useState(false);
  const [pending, setPending] = useState<CallbackCompensationCreate | null>(() => unresolvedCreates.get(key) ?? null);
  const pendingRef = useRef(pending);
  const [busy, setBusy] = useState<'preview' | 'create' | null>(null),
    busyRef = useRef(false);
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now);
  const alive = useRef(true),
    controller = useRef<AbortController | null>(null);
  const batch = useCallbackCompensationBatch(
    { ...scope, scopeKey: context.scopeKey, userId: context.userId },
    open,
    onDenied,
  );
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (!preview) return;
    const remaining = Date.parse(preview.expiresAt) - Date.now();
    if (remaining <= 0) {
      if (now < Date.parse(preview.expiresAt)) setNow(Date.now());
      return;
    }
    const timer = window.setTimeout(() => setNow(Date.now()), Math.min(remaining + 1, 2147483647));
    return () => window.clearTimeout(timer);
  }, [preview, now]);
  const executionReady = callbackCompensationExecutionReady(capabilities);
  const expired = !!preview && Date.parse(preview.expiresAt) <= now;
  const canExecute = callbackCompensationCanExecute({
    capabilities,
    preview,
    itemIds: selected,
    reason,
    acknowledged,
    now,
  });
  const clearPreview = () => {
    setPreview(null);
    setPreviewRange(null);
    setSelected([]);
    setConfirming(false);
    setReason('');
    setAcknowledged(false);
    setError('');
  };
  const startNew = () => {
    // An ambiguous create can only be resolved with its frozen request, never a new preview/key.
    if (busyRef.current || pendingRef.current || !batch.resetResolved()) return;
    clearPreview();
    setRange(callbackCompensationDefaultRange());
    setConfigId(capabilities.configs[0]?.configId ?? '');
    setRunIds('');
    setMode('time');
    setNow(Date.now());
  };
  const editable = () => !busyRef.current && !pendingRef.current && !batch.batchId;
  const updateRange = (
    patch: Partial<typeof range> & { configId?: string; runIds?: string; mode?: 'time' | 'ids' },
  ) => {
    if (!editable()) return;
    clearPreview();
    if (patch.from !== undefined || patch.to !== undefined)
      setRange((current) => ({ from: patch.from ?? current.from, to: patch.to ?? current.to }));
    if (patch.configId !== undefined) setConfigId(patch.configId);
    if (patch.runIds !== undefined) setRunIds(patch.runIds);
    if (patch.mode !== undefined) setMode(patch.mode);
  };
  const generatePreview = async () => {
    if (!editable() || !capabilities.configs.some((config) => config.configId === configId)) return;
    let input: CallbackCompensationRange;
    try {
      input = callbackCompensationRange(configId, range.from, range.to, runIds, mode);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '请检查范围');
      return;
    }
    busyRef.current = true;
    setBusy('preview');
    clearPreview();
    const request = new AbortController();
    controller.current = request;
    try {
      const value = await service.preview(scope, input, request.signal);
      if (!alive.current || request.signal.aborted) return;
      setPreview(value);
      setPreviewRange(input);
      setNow(Date.now());
    } catch (failure) {
      if (!alive.current || request.signal.aborted) return;
      if (callbackCompensationErrorStatus(failure) === 403) onDenied();
      else setError(callbackCompensationErrorMessage(failure, '预览暂时不可用，请稍后重试；不会使用模拟数据。'));
    } finally {
      if (alive.current && !request.signal.aborted) {
        busyRef.current = false;
        setBusy(null);
      }
    }
  };
  const execute = async () => {
    if (busyRef.current || batch.batchId || !executionReady) return;
    let input = pendingRef.current;
    if (!input) {
      if (
        !confirming ||
        !callbackCompensationCanExecute({
          capabilities,
          preview,
          itemIds: selected,
          reason,
          acknowledged,
          now: Date.now(),
        })
      )
        return;
      try {
        input = {
          previewId: preview!.previewId,
          itemIds: [...selected],
          reason: reason.trim(),
          requestKey: requestKey(),
        };
      } catch {
        setError('当前浏览器无法生成安全请求标识，未发起补偿。');
        return;
      }
      unresolvedCreates.set(key, input);
      pendingRef.current = input;
      setPending(input);
    }
    // Freeze before the network call. A timeout/abort/error can never unlock a new preview or request key.
    busyRef.current = true;
    setBusy('create');
    setError('');
    const request = new AbortController();
    controller.current = request;
    try {
      const value = await service.create(scope, input, request.signal);
      if (!alive.current || request.signal.aborted) return;
      batch.accept(value);
      unresolvedCreates.delete(key);
      pendingRef.current = null;
      setPending(null);
    } catch (failure) {
      if (!alive.current || request.signal.aborted) return;
      if (callbackCompensationErrorStatus(failure) === 403) onDenied();
      else
        setError(
          `创建结果尚未确认。${callbackCompensationErrorMessage(
            failure,
            '只能使用同一请求重试，不会重新获得投递资格或新建请求标识。',
          )}`,
        );
    } finally {
      if (alive.current && !request.signal.aborted) {
        busyRef.current = false;
        setBusy(null);
      }
    }
  };
  return {
    open,
    setOpen,
    range,
    configId,
    runIds,
    mode,
    updateRange,
    preview,
    previewRange,
    selected,
    confirming,
    reason,
    acknowledged,
    setReason,
    setAcknowledged,
    pending,
    busy,
    error,
    batch,
    expired,
    executionReady,
    canExecute: canExecute && !busy && !batch.batchId,
    canStartNew: batch.canStartNew && !busy && !pending,
    startNew,
    generatePreview: () => void generatePreview(),
    execute: () => void execute(),
    select: (id: string, checked: boolean) => {
      if (
        !editable() ||
        !executionReady ||
        expired ||
        !preview?.items.some((item) => item.itemId === id && item.status === 'eligible')
      )
        return;
      setSelected((items) => (checked ? [...new Set([...items, id])] : items.filter((item) => item !== id)));
    },
    modifyRange: () => {
      if (editable()) clearPreview();
    },
    confirm: () => {
      if (
        editable() &&
        executionReady &&
        !expired &&
        selected.length &&
        selected.every((id) => preview?.items.some((item) => item.itemId === id && item.status === 'eligible'))
      )
        setConfirming(true);
    },
    back: () => {
      if (editable()) setConfirming(false);
    },
  };
}
export type CallbackCompensationState = ReturnType<typeof useCallbackCompensation>;
