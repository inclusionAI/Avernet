/** @jest-environment jsdom */
// C1 修复验收:V2 副屏轮询必须携带 include_action_log=true——
// rework/attemptCount/execDag/stepTraces 全部依赖 action_log,轮询不带则 V2 派生整体休眠。
import { TaskPanel } from '@/assets/TaskPanel/TaskPanel';
import { act, render } from '@testing-library/react';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

const dashboardEnvelope = {
  code: 0,
  message: 'ok',
  request_id: 'r',
  data: {
    task_id: 't-log',
    run_id: 1,
    status: 'RUNNING',
    needs_attention: false,
    task_type: 'dynamic',
    source_type: 'bot',
    owner_user_id: 'u1',
    owner_bot_id: 'b1',
    task_spec: { metadata: { title: '任务' }, goal: { objective: '', acceptances: [] } },
    create_time: '2026-10-20T10:00:00+08:00',
    finish_time: null,
    loop_round: 1,
    progress: { total: 0, pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
    tasks: [
      {
        node_id: 'n1',
        task_id: 't-log',
        sequence: 1,
        status: 'RUNNING',
        task_spec: { metadata: { title: '节点' }, goal: { objective: '', acceptances: [] } },
        run_info: { action_log: [{ seq: 1, ts: 1700000000000, action: 'dispatch', attempt: 1 }] },
      },
    ],
    relations: [],
  },
};

describe('TaskPanel V2 轮询接线(C1)', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    (global as { fetch: unknown }).fetch = jest.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => dashboardEnvelope,
      }),
    );
  });

  it('dashboard 轮询 URL 携带 include_action_log=true', async () => {
    render(<TaskPanel apiBaseUrl="" taskId="t-log" />);
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10);
    });
    expect((global as { fetch: jest.Mock }).fetch).toHaveBeenCalled();
    const url = (global as { fetch: jest.Mock }).fetch.mock.calls[0][0] as string;
    expect(url).toContain('include_action_log=true');
    expect(url).toContain('task_id=t-log');

    // 停止轮询,避免句柄泄漏
    jest.useRealTimers();
  });
});