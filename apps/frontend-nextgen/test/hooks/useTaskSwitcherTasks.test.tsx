/** @jest-environment jsdom */
// useTaskSwitcherTasks:listTasks 数据源 → 三桶状态映射;失败静默空列表。
import type { TaskListItem } from '@/domain/tasks/models';
import { useTaskSwitcherTasks } from '@/hooks/useTaskSwitcherTasks';
import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

const mockListMyTasks = jest.fn();
const mockNormalizeMyTaskPage = jest.fn((data: unknown) => ({
  items: Array.isArray(data) ? data : [],
  total: Array.isArray(data) ? data.length : 0,
}));
jest.mock('@/services/myTask', () => ({
  listMyTasks: (...args: unknown[]) => mockListMyTasks(...args),
  normalizeMyTaskPage: (data: unknown) => mockNormalizeMyTaskPage(data),
  isEnvelopeFailure: () => false,
}));

const item = (taskId: string, status: string, title: string): TaskListItem =>
  ({
    id: 1,
    task_id: taskId,
    source_type: 'bot',
    owner_user_id: 'u1',
    owner_bot_id: 'b1',
    execution_config: { task_type: 'dynamic' },
    task_spec: { context: { title, background: '' }, goal: { objective: '', acceptances: [] } },
    status,
    gmt_create: '2026-10-20 10:00:00',
    gmt_modified: '2026-10-20 10:00:00',
  }) as unknown as TaskListItem;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('useTaskSwitcherTasks', () => {
  it('listTasks 结果映射三桶状态与中文 label(执行中/待开始/已结束)', async () => {
    mockListMyTasks.mockResolvedValue({
      code: 0,
      message: 'ok',
      data: [
        item('t1', 'EXECUTING', '执行中任务'),
        item('t2', 'REVIEWING', '验收任务'),
        item('t3', 'DRAFTING', '草稿任务'),
        item('t4', 'PENDING', '待执行任务'),
        item('t5', 'DONE', '完成任务'),
        item('t6', 'FAILED', '失败任务'),
        item('t7', 'CANCELLED', '取消任务'),
      ],
      request_id: 'r',
    });
    const { result } = renderHook(() => useTaskSwitcherTasks('u1'));
    await waitFor(() => expect(result.current.loading).toBe(false));

    const byId = new Map(result.current.items.map((i) => [i.taskId, i]));
    expect(byId.get('t1')).toMatchObject({ status: 'running', statusLabel: '执行中' });
    expect(byId.get('t2')).toMatchObject({ status: 'running' });
    expect(byId.get('t3')).toMatchObject({ status: 'pending', statusLabel: '待开始' });
    expect(byId.get('t4')).toMatchObject({ status: 'pending' });
    expect(byId.get('t5')).toMatchObject({ status: 'done', statusLabel: '已完成' });
    expect(byId.get('t6')).toMatchObject({ status: 'done', statusLabel: '失败' });
    expect(byId.get('t7')).toMatchObject({ status: 'done', statusLabel: '已取消' });
    expect(byId.get('t1')?.title).toBe('执行中任务');
  });

  it('接口失败 → items 静默空列表(不抛错)', async () => {
    mockListMyTasks.mockRejectedValue(new Error('network down'));
    const { result } = renderHook(() => useTaskSwitcherTasks('u1'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toEqual([]);
  });

  it('enabled=false 或无 userId 不发起查询', async () => {
    const { result } = renderHook(() => useTaskSwitcherTasks(undefined, { enabled: false }));
    expect(result.current.loading).toBe(false);
    expect(mockListMyTasks).not.toHaveBeenCalled();
  });
});