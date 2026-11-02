import { applyTaskStatuses, type TaskStatusSnapshot } from '@/domain/groupTasks/statusProjection';
import type { ParticipantView } from '@/domain/collaboration';
import { isTerminalTask, projectGroupTasks, type GroupTask } from '@/domain/groupTasks/projection';
import { cancelQueuedDelivery, queryTaskDeliveries, queryWorkerTasks, cancelWorkerTask } from '@/services/workspace/messageDeliveryService';
import { useMessageDeliveryStore } from '@/stores/messageDeliveryStore';
import type { ChatMessage } from '@tc-chat/core';
import { useEffect, useMemo, useRef, useState } from 'react';

/** Session-scoped, rebuildable task projection. Never infer completion from an individual text segment. */
export function useManagerWorkerTasks(sessionId: string, messages: ChatMessage[], participants: ParticipantView[]) {
  const deliveries = useMessageDeliveryStore((state) => state.deliveriesBySessionId[sessionId]);
  const baseTasks = useMemo(
    () => projectGroupTasks(Object.values(deliveries ?? {}), messages, participants),
    [deliveries, messages, participants],
  );
  const [error, setError] = useState<string>();
  const [statusState, setStatusState] = useState<{ sessionId: string; rows: Record<string, TaskStatusSnapshot> }>({ sessionId, rows: {} });
  const tasks = useMemo(() => applyTaskStatuses(baseTasks, statusState.sessionId === sessionId ? statusState.rows : {}), [baseTasks, statusState, sessionId]);
  // Query at most 100 loaded assignments, newest first; do not poll arbitrary historical refs.
  const taskRefs = JSON.stringify(baseTasks.filter((task) => task.intentId).slice(0, 100).map((task) => task.intentId));
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const refs = JSON.parse(taskRefs) as string[];
        if (refs.length) {
          const rows = await queryWorkerTasks(refs);
          const observedAt = Date.now();
          if (!disposed) {
            setStatusState({ sessionId, rows: Object.fromEntries(rows.map((row) => [row.ref, { row, observedAt }])) });
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
  }, [sessionId, taskRefs]);
  const [read, setRead] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [uncertain, setUncertain] = useState<Record<string, number>>({});
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
  // Source IDs, rather than streaming content, drive recovery queries (bounded by the API's 100-ID limit).
  const messageIds = JSON.stringify([...new Set([
    ...tasks.filter((task) => !isTerminalTask(task.status)).map((task) => task.sourceMessageId),
    ...messages.map((message) => message.id).slice(-100),
  ])]);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      const ids = JSON.parse(messageIds) as string[];
      try {
        if (ids.length) {
          const rows = await queryTaskDeliveries(sessionId, ids);
          if (!disposed) {
            for (const row of rows) if (row.task) useMessageDeliveryStore.getState().upsertDelivery(sessionId, row);
          }
        }
      } catch {
        if (!disposed) setError('任务状态更新失败，正在重试');
      } finally {
        if (!disposed) timer = setTimeout(refresh, 10_000);
      }
    };
    void refresh();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [sessionId, messageIds]);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const displayed = tasks.map((task) => ({
    ...task,
    lastActivityAt: Math.max(task.lastActivityAt, task.runId ? streamActivity[task.runId] ?? 0 : 0),
    status:
      uncertain[task.taskRef] !== undefined && task.version <= uncertain[task.taskRef]
        ? ('unknown' as const)
        : task.status,
  }));
  const signature = (task: GroupTask) => `${task.status}:${task.finishedAt ?? (task.source === 'status' ? 0 : task.version)}`;
  const unread = displayed.filter((task) => isTerminalTask(task.status) && read[task.taskRef] !== signature(task));
  const markRead = () =>
    setRead((previous) => ({
      ...previous,
      ...Object.fromEntries(displayed.filter((t) => isTerminalTask(t.status)).map((t) => [t.taskRef, signature(t)])),
    }));
  const cancelTask = async (task: GroupTask) => {
    if ((!task.deliveryId && !task.intentId) || pendingRef.current.has(task.taskRef)) return;
    pendingRef.current.add(task.taskRef);
    setPending(new Set(pendingRef.current));
    setUncertain((previous) => ({ ...previous, [task.taskRef]: task.version }));
    try {
      if (!task.deliveryId) {
        const row = await cancelWorkerTask(task.intentId!);
        const observedAt = Date.now();
        setStatusState((previous) => ({ sessionId, rows: { ...(previous.sessionId === sessionId ? previous.rows : {}), [row.ref]: { row, observedAt } } }));
        setError(row.status === 'cancel_failed' ? '停止失败，请稍后重试' : undefined);
        return;
      }
      const outcome = await cancelQueuedDelivery(task.sourceMessageId, task.deliveryId, sessionId);
      for (const result of outcome.results)
        useMessageDeliveryStore.getState().upsertDelivery(sessionId, result.delivery);
      setError(outcome.firstError ?? undefined);
    } catch {
      setError('停止结果未确认，请等待状态更新');
    } finally {
      pendingRef.current.delete(task.taskRef);
      setPending(new Set(pendingRef.current));
    }
  };
  return { tasks: displayed, unread, markRead, cancelTask, pending, error, now };
}
