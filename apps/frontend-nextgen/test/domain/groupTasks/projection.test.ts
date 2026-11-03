import { applyTaskStatuses } from '@/domain/groupTasks/statusProjection';
import type { DeliveryStatusView } from '@/domain/collaboration';
import {
  deliveryTaskStatus,
  formatTaskDuration,
  isTaskUiOnlyMessage,
  projectGroupTasks,
} from '@/domain/groupTasks/projection';
import type { ChatMessage } from '@tc-chat/core';

const delivery = (ref = 'T-12345678', status: DeliveryStatusView['status'] = 'running'): DeliveryStatusView => ({
  delivery_id: `d-${ref}`,
  message_id: `m-${ref}`,
  target_bot_id: 'worker',
  flow_kind: 'task',
  kind: 'send',
  status,
  state_version: 1,
  run_id: 'run-1',
  wait_reason: null,
  admission_error: null,
  task: {
    task_ref: ref,
    summary: '分析日志',
    manager_id: 'manager',
    worker_id: 'worker',
    worker_name: '日志 Bot',
    queued_at: 1000,
    started_at: 2000,
    last_activity_at: 3000,
    finished_at: null,
    timed_out: false,
  },
});

describe('manager-worker task projection', () => {
  it('keeps multiple tasks for one worker, and aggregates all streaming segments by task ref', () => {
    const messages = [1, 2, 3].map(
      (id) =>
        ({
          id: `${id}`,
          role: 'assistant',
          content: 'segment',
          status: 'streaming',
          extra: { runId: 'run-1' },
          createdAt: 4000 + id,
        } as ChatMessage),
    );
    const tasks = projectGroupTasks([delivery(), delivery('T-87654321'), delivery()], messages, []);
    expect(tasks).toHaveLength(2);
    expect(tasks[0].lastActivityAt).toBe(4003);
    expect(tasks[0].workerName).toBe('日志 Bot');
  });
  it.each([
    ['queued', 'queued'],
    ['dispatching', 'queued'],
    ['running', 'running'],
    ['completed', 'completed'],
    ['failed', 'failed'],
    ['cancelled', 'cancelled'],
    ['expired', 'unknown'],
    ['unknown', 'unknown'],
    ['cancel_unknown', 'unknown'],
    ['cancelling', 'unknown'],
    ['consumed', 'unknown'],
    ['rejected_capacity', 'failed'],
  ] as const)('maps %s to %s without inventing a terminal state', (status, expected) => {
    expect(deliveryTaskStatus(delivery('T-1', status))).toBe(expected);
  });
  it('only confirms timeout after cancellation is confirmed', () => {
    const row = delivery('T-1', 'cancel_unknown');
    row.task!.timed_out = true;
    expect(deliveryTaskStatus(row)).toBe('unknown');
    row.status = 'cancelled';
    expect(deliveryTaskStatus(row)).toBe('timed_out');
  });
  it('does not guess tasks for old or result deliveries', () => {
    expect(projectGroupTasks([{ ...delivery(), task: undefined }], [], [])).toEqual([]);
    expect(projectGroupTasks([{ ...delivery(), target_bot_id: 'manager' }], [], [])).toEqual([]);
  });
  it('uses newer versions and preserves terminal state against reordered updates', () => {
    const current = { ...delivery('T-1', 'completed'), state_version: 3 };
    expect(projectGroupTasks([current, delivery('T-1')], [], [])[0].status).toBe('completed');
  });
  it('computes durations from server timestamps, including reconnects and clock skew', () => {
    expect(formatTaskDuration(2000, 64000)).toBe('01:02');
    expect(formatTaskDuration(2000, 65000)).toBe('01:03');
    expect(formatTaskDuration(2000, 1000)).toBe('00:00');
    expect(formatTaskDuration(undefined, 5000)).toBe('—');
  });
  it('hides explicit UI-only task statuses but preserves real system and model messages', () => {
    const message = {
      id: 'ui-state',
      status: 'history',
      role: 'system',
      content: '任务已完成',
      extra: { metadata: { ui_only: true, task_ref: 'T-1' } },
    } as ChatMessage;
    expect(isTaskUiOnlyMessage(message)).toBe(true);
    expect(
      isTaskUiOnlyMessage({
        ...message,
        extra: {},
        content: '[任务状态] 待回复: Bot | 已回复: - | 失败: - | 超时: - | 已中断: -',
      }),
    ).toBe(true);
    expect(isTaskUiOnlyMessage({ ...message, role: 'assistant' })).toBe(false);
    expect(isTaskUiOnlyMessage({ ...message, extra: {} })).toBe(false);
  });
});


describe('authoritative worker task status', () => {
  const receipt = { taskRef: 'T-12345678', intentId: `bcs_intent_${'12345678'.repeat(4)}`,
    source: 'receipt', summary: '检查日志', workerName: 'ray', status: 'unknown',
    sourceMessageId: 'manager-result', lastActivityAt: 1000, version: 0 } as const;
  it('joins on the full intent, upgrades one receipt, and preserves source identity', () => {
    const result = applyTaskStatuses([receipt], { [receipt.intentId]: {
      row: { ref: receipt.intentId, status: 'completed' }, observedAt: 5000,
    } });
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ source: 'status', status: 'completed', sourceMessageId: 'manager-result', intentId: receipt.intentId });
    expect(result[0].finishedAt).toBeUndefined();
  });
  it.each(['unknown', 'not_found', 'ambiguous'] as const)('does not turn %s into a completed task', (status) => {
    const [result] = applyTaskStatuses([receipt], { [receipt.intentId]: { row: { ref: receipt.intentId, status }, observedAt: 5000 } });
    expect(result.source).toBe('receipt');
    expect(result.status).toBe('unknown');
    expect(result.statusNote).toBeTruthy();
  });
  it('uses server relative runtime instead of receipt creation time', () => {
    const [result] = applyTaskStatuses([receipt], { [receipt.intentId]: {
      row: { ref: receipt.intentId, status: 'running', running_s: 2, idle_s: 1 }, observedAt: 10000,
    } });
    expect(result.startedAt).toBe(8000);
    expect(result.lastActivityAt).toBe(9000);
  });
});


it.each(['completed', 'failed', 'cancelled'] as const)('preserves persisted %s after correlation expires', (terminal) => {
  const [task] = projectGroupTasks([{ ...delivery('T-12345678', terminal), task: { ...delivery().task!, finished_at: 4000 } }], [], []);
  task.intentId = `bcs_intent_${'a'.repeat(32)}`;
  for (const status of ['not_found', 'unknown', 'queued', 'running', 'already_finished', 'ambiguous', 'cancel_failed', 'cancelled'] as const) {
    const [result] = applyTaskStatuses([task], { [task.intentId]: {
      row: { ref: task.intentId, status }, observedAt: 100000,
    } });
    expect(result).toEqual(task);
  }
});

it('keeps expired delivery execution uncertain until task status confirms termination', () => {
  const expired = delivery('T-12345678', 'expired');
  expired.task!.finished_at = 4000;
  expired.task!.timed_out = true;
  const [task] = projectGroupTasks([expired], [], []);
  task.intentId = `bcs_intent_${'a'.repeat(32)}`;
  expect(task.status).toBe('unknown');
  expect(task.finishedAt).toBeUndefined();
  const [uncertain] = applyTaskStatuses([task], { [task.intentId]: {
    row: { ref: task.intentId, status: 'unknown' }, observedAt: 5000,
  } });
  expect(uncertain.status).toBe('unknown');
  expect(uncertain.finishedAt).toBeUndefined();
  const [confirmed] = applyTaskStatuses([uncertain], { [task.intentId]: {
    row: { ref: task.intentId, status: 'timed_out' }, observedAt: 6000,
  } });
  expect(confirmed.status).toBe('timed_out');
});
