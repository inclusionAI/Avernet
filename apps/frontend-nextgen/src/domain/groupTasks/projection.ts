import type { DeliveryStatusView, ParticipantView } from '@/domain/collaboration';
import type { ChatMessage } from '@tc-chat/core';
import { projectTaskReceipts } from './receiptProjection';

export type GroupTaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'timed_out' | 'unknown';
export interface GroupTask {
  taskRef: string;
  source: 'delivery' | 'receipt' | 'status';
  /** Complete assignment identity retained from the authenticated tool result. */
  intentId?: string;
  summary: string;
  workerId?: string;
  workerName: string;
  status: GroupTaskStatus;
  queuedAt?: number;
  startedAt?: number;
  lastActivityAt: number;
  finishedAt?: number;
  sourceMessageId: string;
  deliveryId?: string;
  runId?: string;
  errorMessage?: string;
  statusNote?: string;
  version: number;
}

/** Short references are display labels, never task identities. */
export function taskIdentity(task: GroupTask): string {
  return task.intentId ?? (task.deliveryId ? `delivery:${task.deliveryId}` : `receipt:${task.sourceMessageId}:${task.taskRef}`);
}

export function completeIntentId(value: unknown): string | undefined {
  return typeof value === 'string' && /^bcs_intent_[a-f0-9]{32}$/i.test(value) ? value.toLowerCase() : undefined;
}

export const taskStatusLabels: Record<GroupTaskStatus, string> = {
  queued: '排队中',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  timed_out: '已超时',
  unknown: '状态待确认',
};
export function isTerminalTask(status: GroupTaskStatus): boolean {
  return ['completed', 'failed', 'cancelled', 'timed_out'].includes(status);
}
export function deliveryTaskStatus(delivery: DeliveryStatusView): GroupTaskStatus {
  if (delivery.status === 'cancelled' && delivery.task?.timed_out) return 'timed_out';
  switch (delivery.status) {
    case 'queued':
    case 'dispatching':
      return 'queued';
    case 'running':
      return 'running';
    case 'completed':
    case 'failed':
    case 'cancelled':
      return delivery.status;
    case 'expired':
      // Queue expiry does not confirm that an uncertain send or cancellation stopped the Worker.
      return 'unknown';
    case 'rejected_capacity':
      return 'failed';
    default:
      return 'unknown';
  }
}

/** Explicit task references only. Worker/result segments never create tasks. */
export function projectGroupTasks(
  deliveries: DeliveryStatusView[],
  messages: ChatMessage[],
  participants: ParticipantView[],
): GroupTask[] {
  const byRef = new Map(projectTaskReceipts(messages, participants).map((task) => [taskIdentity(task), task]));
  for (const delivery of deliveries) {
    const task = delivery.task;
    if (!task?.task_ref || delivery.flow_kind !== 'task' || task.worker_id !== delivery.target_bot_id) continue;
    const intentId = completeIntentId(task.intent_id);
    const identity = intentId ?? `delivery:${delivery.delivery_id}`;
    const current = byRef.get(identity);
    if (
      current?.deliveryId &&
      (current.lastActivityAt > task.last_activity_at ||
        (current.lastActivityAt === task.last_activity_at && current.version >= delivery.state_version))
    )
      continue;
    const status = deliveryTaskStatus(delivery);
    byRef.set(identity, {
      taskRef: task.task_ref,
      intentId,
      source: 'delivery',
      summary: task.summary || delivery.content_preview || '未记录任务摘要',
      workerId: task.worker_id,
      workerName: participants.find((p) => p.actorId === task.worker_id)?.name || task.worker_name || 'Worker',
      status,
      queuedAt: task.queued_at,
      startedAt: task.started_at ?? undefined,
      lastActivityAt: task.last_activity_at,
      finishedAt: isTerminalTask(status) ? task.finished_at ?? undefined : undefined,
      sourceMessageId: delivery.message_id,
      deliveryId: delivery.delivery_id,
      runId: delivery.run_id ?? undefined,
      errorMessage: delivery.admission_error ?? undefined,
      version: delivery.state_version,
    });
  }
  for (const task of byRef.values()) {
    for (const message of messages) {
      if (task.runId && message.extra?.runId === task.runId && typeof message.createdAt === 'number')
        task.lastActivityAt = Math.max(task.lastActivityAt, message.createdAt);
    }
  }
  return [...byRef.values()].sort((a, b) => (b.queuedAt ?? 0) - (a.queuedAt ?? 0));
}

export function formatTaskDuration(start: number | undefined, end: number): string {
  if (start === undefined) return '—';
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

/** Hide only explicit UI status records; real model-facing collaboration messages survive. */
export function isTaskUiOnlyMessage(message: ChatMessage): boolean {
  const metadata = message.extra?.metadata as Record<string, unknown> | undefined;
  if (message.role !== 'system') return false;
  return (
    (metadata?.ui_only === true && typeof metadata?.task_ref === 'string') ||
    /^\[任务状态\] 待回复: [^\n]* \| 已回复: [^\n]* \| 失败: [^\n]* \| 超时: [^\n]* \| 已中断: [^\n]*$/.test(
      message.content,
    )
  );
}
