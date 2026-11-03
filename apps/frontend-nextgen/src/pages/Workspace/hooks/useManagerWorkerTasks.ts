import { applyTaskStatuses, type TaskStatusSnapshot } from '@/domain/groupTasks/statusProjection';
import type { ParticipantView } from '@/domain/collaboration';
import { isTerminalTask, projectGroupTasks, taskIdentity, completeIntentId, type GroupTask } from '@/domain/groupTasks/projection';
import { queryTaskDeliveries, queryWorkerTasks, cancelWorkerTask } from '@/services/workspace/messageDeliveryService';
import { useMessageDeliveryStore } from '@/stores/messageDeliveryStore';
import type { ChatMessage } from '@tc-chat/core';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

/** Session-scoped, rebuildable task projection. Never infer completion from an individual text segment. */
export function useManagerWorkerTasks(sessionId: string, messages: ChatMessage[], participants: ParticipantView[], connected = true) {
  const deliveries = useMessageDeliveryStore((state) => state.deliveriesBySessionId[sessionId]);
  const baseTasks = useMemo(
    () => projectGroupTasks(Object.values(deliveries ?? {}), messages, participants),
    [deliveries, messages, participants],
  );
  const [uncertain, setUncertain] = useState<Record<string, number>>({});
  const requestSequence = useRef(0);
  const cancellationBarrier = useRef(new Map<string, number>());
  const currentTasks = useRef(baseTasks);
  currentTasks.current = baseTasks;
  const clearConfirmed = useCallback((refs: string[], started: number) => {
    const confirmed = new Set(currentTasks.current.filter((task) =>
      refs.includes(taskIdentity(task)),
    ).map(taskIdentity));
    setUncertain((previous) => {
      const retained = Object.entries(previous).filter(([ref, after]) => !confirmed.has(ref) || started <= after);
      return retained.length === Object.keys(previous).length ? previous : Object.fromEntries(retained);
    });
  }, []);
  useEffect(() => { setUncertain({}); cancellationBarrier.current.clear(); }, [sessionId]);
  const [error, setError] = useState<string>();
  const [statusState, setStatusState] = useState<{ sessionId: string; rows: Record<string, TaskStatusSnapshot> }>({ sessionId, rows: {} });
  const tasks = useMemo(() => applyTaskStatuses(baseTasks, statusState.sessionId === sessionId ? statusState.rows : {}), [baseTasks, statusState, sessionId]);
  // Query at most 100 loaded assignments, newest first; do not poll arbitrary historical refs.
  const taskRefs = JSON.stringify(tasks.filter((task) => task.intentId && !isTerminalTask(task.status)).slice(0, 100).map((task) => task.intentId));
  useEffect(() => {
    if (!connected || taskRefs === '[]') return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      const started = ++requestSequence.current;
      try {
        const refs = JSON.parse(taskRefs) as string[];
        if (refs.length) {
          const rows = (await queryWorkerTasks(refs)).filter((row) => started > (cancellationBarrier.current.get(row.ref) ?? -1));
          const observedAt = Date.now();
          if (!disposed) {
            setStatusState((previous) => ({ sessionId, rows: { ...(previous.sessionId === sessionId ? previous.rows : {}), ...Object.fromEntries(rows.map((row) => [row.ref, { row, observedAt }])) } }));
            clearConfirmed(rows.filter((row) => ['queued', 'running', 'completed', 'failed', 'cancelled', 'timed_out'].includes(row.status)).map((row) => row.ref), started);
            setError(undefined);
          }
        }
      } catch {
        if (!disposed) setError('任务状态查询失败，正在重试');
      } finally {
        if (!disposed) timer = setTimeout(refresh, 10_000);
      }
    };
    void refresh();
    return () => { disposed = true; clearTimeout(timer); };
  }, [sessionId, taskRefs, connected, clearConfirmed]);
  const [read, setRead] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [now, setNow] = useState(Date.now);
  const pendingRef = useRef(new Set<string>());
  const observedRuns = useRef(new Map<string, string>());
  const [streamActivity, setStreamActivity] = useState<Record<string, number>>({});
  useEffect(() => {
    const signatures = new Map<string, string>();
    for (const message of messages) {
      const runId = message.extra?.runId;
      if (typeof runId !== 'string' || message.status !== 'streaming') continue;
      signatures.set(
        runId,
        `${signatures.get(runId) ?? ''}|${message.id}:${message.content.length}:${JSON.stringify(
          message.blocks ?? [],
        )}`,
      );
    }
    const updates: Record<string, number> = {};
    for (const [runId, signature] of signatures) {
      if (observedRuns.current.has(runId) && observedRuns.current.get(runId) !== signature) updates[runId] = Date.now();
    }
    observedRuns.current = signatures;
    if (Object.keys(updates).length) setStreamActivity((previous) => ({ ...previous, ...updates }));
  }, [messages]);
  // Scan each loaded history source once per connection. Only unfinished tasks recur.
  const historyIds = JSON.stringify([...new Set(messages.map((message) => message.id))]);
  const activeIds = JSON.stringify([...new Set(tasks.filter((task) => !isTerminalTask(task.status)).map((task) => task.sourceMessageId))]);
  const recoveredSources = useRef(new Set<string>());
  useEffect(() => { recoveredSources.current.clear(); }, [sessionId, connected]);
  useEffect(() => {
    if (!connected) return;
    const loadedSources: string[] = JSON.parse(historyIds);
    const activeSources: string[] = JSON.parse(activeIds);
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      const started = ++requestSequence.current;
      const startedAt = Date.now();
      const historical = loadedSources.filter((id) => !recoveredSources.current.has(id));
      const ids = [...new Set([...historical, ...activeSources])];
      try {
        if (ids.length) {
          const rows = (await queryTaskDeliveries(sessionId, ids)).filter((row) => started > (cancellationBarrier.current.get(completeIntentId(row.task?.intent_id) ?? `delivery:${row.delivery_id}`) ?? -1));
          if (!disposed) {
            for (const id of historical) recoveredSources.current.add(id);
            setError((previous) => previous === '任务状态更新失败，正在重试' ? undefined : previous);
            for (const row of rows) if (row.task) useMessageDeliveryStore.getState().upsertDelivery(sessionId, row);
            const confirmed = projectGroupTasks(rows, [], []).filter((task) => task.status !== 'unknown').map(taskIdentity);
            clearConfirmed(confirmed, started);
            // A newer delivery read can recover an uncertain task-API response.
            setStatusState((previous) => {
              if (previous.sessionId !== sessionId) return previous;
              const next = { ...previous.rows };
              for (const task of currentTasks.current) {
                const snapshot = task.intentId ? next[task.intentId] : undefined;
                if (task.intentId && confirmed.includes(taskIdentity(task)) && snapshot && snapshot.observedAt <= startedAt &&
                    ['unknown', 'not_found', 'ambiguous', 'already_finished', 'cancel_failed'].includes(snapshot.row.status)) {
                  delete next[task.intentId];
                }
              }
              return Object.keys(next).length === Object.keys(previous.rows).length ? previous : { sessionId, rows: next };
            });
          }
        }
      } catch {
        if (!disposed) setError('任务状态更新失败，正在重试');
      } finally {
        if (!disposed && (activeSources.length > 0 || loadedSources.some((id) => !recoveredSources.current.has(id)))) {
          timer = setTimeout(refresh, 10_000);
        }
      }
    };
    void refresh();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [sessionId, historyIds, activeIds, connected, clearConfirmed]);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const displayed = tasks.map((task) => ({
    ...task,
    lastActivityAt: Math.max(task.lastActivityAt, task.runId ? streamActivity[task.runId] ?? 0 : 0),
    status:
      uncertain[taskIdentity(task)] !== undefined && !isTerminalTask(task.status)
        ? ('unknown' as const)
        : task.status,
  }));
  const signature = (task: GroupTask) => `${task.status}:${task.finishedAt ?? (task.source === 'status' ? 0 : task.version)}`;
  const unread = displayed.filter((task) => isTerminalTask(task.status) && read[taskIdentity(task)] !== signature(task));
  const markRead = () =>
    setRead((previous) => ({
      ...previous,
      ...Object.fromEntries(displayed.filter((t) => isTerminalTask(t.status)).map((t) => [taskIdentity(t), signature(t)])),
    }));
  const cancelTask = async (task: GroupTask) => {
    if (!task.intentId || isTerminalTask(task.status) || pendingRef.current.has(taskIdentity(task))) return;
    cancellationBarrier.current.set(taskIdentity(task), Infinity);
    pendingRef.current.add(taskIdentity(task));
    setPending(new Set(pendingRef.current));
    setUncertain((previous) => ({ ...previous, [taskIdentity(task)]: Infinity }));
    try {
      const row = await cancelWorkerTask(task.intentId);
      const observedAt = Date.now();
      setStatusState((previous) => ({ sessionId, rows: { ...(previous.sessionId === sessionId ? previous.rows : {}), [row.ref]: { row, observedAt } } }));
      if (['queued', 'running', 'completed', 'failed', 'cancelled', 'timed_out'].includes(row.status)) {
        setUncertain((previous) => { const next = { ...previous }; delete next[taskIdentity(task)]; return next; });
      }
      setError(row.status === 'cancel_failed' ? '停止失败，请稍后重试' : undefined);
    } catch {
      setError('停止结果未确认，请等待状态更新');
    } finally {
      const after = ++requestSequence.current;
      cancellationBarrier.current.set(taskIdentity(task), after);
      setUncertain((previous) => previous[taskIdentity(task)] === undefined ? previous : { ...previous, [taskIdentity(task)]: after });
      pendingRef.current.delete(taskIdentity(task));
      setPending(new Set(pendingRef.current));
    }
  };
  return { tasks: displayed, unread, markRead, cancelTask, pending, error, now };
}
