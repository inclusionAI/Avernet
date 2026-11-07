import { isTerminalTask, taskIdentity, type GroupTask } from './projection';

export interface WorkerTaskStatusView {
  ref: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'timed_out' |
    'unknown' | 'not_found' | 'ambiguous' | 'already_finished' | 'cancel_failed';
  running_s?: number;
  idle_s?: number;
}


export interface TaskStatusSnapshot { row: WorkerTaskStatusView; observedAt: number }

/** Only an authoritative task status upgrades a stored receipt to an execution task. */
export function applyTaskStatuses(tasks: GroupTask[], snapshots: Record<string, TaskStatusSnapshot>): GroupTask[] {
  return tasks.map((task) => {
    const snapshot = snapshots[taskIdentity(task)];
    if (!snapshot || (task.source === 'delivery' && task.lastActivityAt > snapshot.observedAt)) return task;
    const { row, observedAt } = snapshot;
    // A response arrival time cannot establish freshness over a persisted terminal event.
    if (task.source === 'delivery' && isTerminalTask(task.status)) return task;
    if (['not_found', 'ambiguous', 'unknown', 'already_finished', 'cancel_failed'].includes(row.status)) {
      const notes: Record<string, string> = {
        not_found: '未查到任务关联记录，可能尚未建立或已超过 7 天缓存保留期，无法确认执行状态。',
        ambiguous: '任务引用匹配到多个任务，无法确认状态。',
        unknown: '服务端尚无法确认任务执行状态。',
      };
      return { ...task, status: 'unknown', statusNote: notes[row.status] ?? '操作结果待确认。' };
    }
    return {
      ...task,
      source: task.source === 'delivery' ? 'delivery' : 'status',
      status: row.status,
      // Relative durations are sampled at query time; no fabricated terminal timestamp.
      startedAt: row.status === 'running' && row.running_s !== undefined ? observedAt - row.running_s * 1000 : task.startedAt,
      lastActivityAt: row.idle_s !== undefined ? observedAt - row.idle_s * 1000 : task.lastActivityAt,
      statusNote: undefined,
      version: observedAt,
    } as GroupTask;
  });
}
