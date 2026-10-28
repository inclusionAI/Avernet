/** @jest-environment jsdom */
// useTaskSwitcherTasks:listTasks 数据源 → 三桶状态映射;失败静默空列表。
import type { TaskDashboardResponse } from '@/assets/TaskPanel/contract';
import type { TaskListItem } from '@/domain/tasks/models';
import {
  buildTaskConversationNavigationTarget,
  filterTasksForCurrentConversation,
  resolveTaskMainSessionId,
  useTaskSwitcherTasks,
} from '@/hooks/useTaskSwitcherTasks';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { useTaskUpdateAttentionStore } from '@/stores/taskUpdateAttentionStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

const mockListMyTasks = jest.fn();
const mockDashboardTask = jest.fn();
const mockNormalizeMyTaskPage = jest.fn((data: unknown) => ({
  items: Array.isArray(data) ? data : [],
  total: Array.isArray(data) ? data.length : 0,
}));
jest.mock('@/services/backendApi/tasks/taskController', () => ({
  dashboardTask: (...args: unknown[]) => mockDashboardTask(...args),
}));

jest.mock('@/services/myTask', () => ({
  listMyTasks: (...args: unknown[]) => mockListMyTasks(...args),
  normalizeMyTaskPage: (data: unknown) => mockNormalizeMyTaskPage(data),
  isEnvelopeFailure: () => false,
}));

const item = (
  taskId: string,
  status: string,
  title: string,
  options: { sessionId?: string; legacySessionId?: string } = { sessionId: 'session-shared' },
): TaskListItem =>
  ({
    id: 1,
    task_id: taskId,
    source_type: 'bot',
    owner_user_id: 'u1',
    owner_bot_id: 'b1',
    execution_config: { task_type: 'dynamic', main_session_id: options.sessionId },
    task_spec: {
      context: {
        title,
        background: '',
        extend_props: {
          teamclaw_context: { main_session_id: options.legacySessionId },
        },
      },
      goal: { objective: '', acceptances: [] },
    },
    status,
    gmt_create: '2026-10-20 10:00:00',
    gmt_modified: '2026-10-20 10:00:00',
  } as unknown as TaskListItem);

beforeEach(() => {
  jest.clearAllMocks();
  useTaskUpdateAttentionStore.getState().resetAll();
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
    const { result } = renderHook(() => useTaskSwitcherTasks('u1', { currentTaskId: 't1' }));
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
    expect(mockListMyTasks).toHaveBeenCalledWith({ user_id: 'u1' });
  });

  it('只返回与当前任务拥有相同现代 main_session_id 的任务', async () => {
    mockListMyTasks.mockResolvedValue({
      code: 0,
      message: 'ok',
      data: [
        item('t1', 'EXECUTING', '当前任务', { sessionId: 'session-a' }),
        item('t2', 'DEFINED', '同会话任务', { sessionId: 'session-a' }),
        item('t3', 'DONE', '其它会话任务', { sessionId: 'session-b' }),
      ],
      request_id: 'r',
    });

    const { result } = renderHook(() => useTaskSwitcherTasks('u1', { currentTaskId: 't1' }));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.items.map((record) => record.taskId)).toEqual(['t1', 't2']);
    expect(result.current.records.map((record) => record.task_id)).toEqual(['t1', 't2']);
  });

  it('历史任务可回退使用 teamclaw_context.main_session_id', () => {
    const records = [
      item('t1', 'EXECUTING', '当前任务', { legacySessionId: 'legacy-session' }),
      item('t2', 'DONE', '同会话历史任务', { legacySessionId: 'legacy-session' }),
      item('t3', 'DONE', '其它任务', { legacySessionId: 'other-session' }),
    ];

    expect(resolveTaskMainSessionId(records[0])).toBe('legacy-session');
    expect(filterTasksForCurrentConversation(records, 't1').map((record) => record.task_id)).toEqual(['t1', 't2']);
  });

  it('当前任务没有会话标识时只保留自己,不聚合其它无会话任务', () => {
    const records = [item('t1', 'EXECUTING', '当前任务', {}), item('t2', 'DONE', '其它无会话任务', {})];

    expect(filterTasksForCurrentConversation(records, 't1').map((record) => record.task_id)).toEqual(['t1']);
  });

  it('列表中找不到当前任务时不展示切换项', () => {
    const records = [item('t2', 'DONE', '其它任务', { sessionId: 'session-a' })];
    expect(filterTasksForCurrentConversation(records, 'unknown')).toEqual([]);
  });

  it('接口失败 → items 静默空列表(不抛错)', async () => {
    mockListMyTasks.mockRejectedValue(new Error('network down'));
    const { result } = renderHook(() => useTaskSwitcherTasks('u1', { currentTaskId: 't1' }));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toEqual([]);
    expect(result.current.records).toEqual([]);
  });

  it('非焦点任务变化时打点提醒,焦点任务变化且用户选中后清除徽标', async () => {
    jest.useFakeTimers();
    try {
      const current = item('t1', 'EXECUTING', '焦点任务');
      const other = item('t2', 'EXECUTING', '后台任务');
      const page = (records: TaskListItem[]) => ({ code: 0, message: 'ok', data: records, request_id: 'r' });
      mockListMyTasks.mockResolvedValueOnce(page([current, other])).mockResolvedValueOnce(
        page([
          current,
          {
            ...other,
            status: 'DONE',
            gmt_modified: '2026-10-20 10:01:00',
          } as TaskListItem,
        ]),
      );
      const { result } = renderHook(() => useTaskSwitcherTasks('u1', { currentTaskId: 't1' }));
      await act(async () => undefined);
      await waitFor(() => expect(result.current.loading).toBe(false));

      expect(result.current.items.map((record) => record.hasUnhandledUpdate)).toEqual([false, false]);

      await act(async () => {
        jest.advanceTimersByTime(60_000);
      });
      const updated = result.current.items.find((record) => record.taskId === 't2');
      expect(updated?.hasUnhandledUpdate).toBe(true);
      expect(result.current.items.find((record) => record.taskId === 't1')?.hasUnhandledUpdate).toBe(false);

      act(() => useTaskSidePanelStore.getState().setActiveTask('t2'));
      expect(result.current.items.find((record) => record.taskId === 't2')?.hasUnhandledUpdate).toBe(false);
    } finally {
      jest.useRealTimers();
    }
  });

  it('enabled=false、无 userId 或无 currentTaskId 时不发起查询', async () => {
    const { result } = renderHook(() => useTaskSwitcherTasks(undefined, { enabled: false }));
    expect(result.current.loading).toBe(false);
    expect(mockListMyTasks).not.toHaveBeenCalled();

    const { result: missingTaskResult } = renderHook(() => useTaskSwitcherTasks('u1'));
    expect(missingTaskResult.current.loading).toBe(false);
    expect(mockListMyTasks).not.toHaveBeenCalled();
  });
});

describe('buildTaskConversationNavigationTarget', () => {
  const dashboardWithFirstNode = (node: Partial<TaskDashboardResponse['tasks'][number]>): TaskDashboardResponse =>
    ({
      task_id: 't1',
      run_id: 1,
      status: 'EXECUTING',
      needs_attention: false,
      task_type: 'dynamic',
      source_type: 'bot',
      owner_user_id: 'u1',
      owner_bot_id: 'b1',
      task_spec: {
        context: { title: '当前任务', background: '', extend_props: {} },
        goal: { objective: '', acceptances: [] },
      },
      create_time: '2026-10-20 10:00:00',
      finish_time: null,
      loop_round: 1,
      progress: {
        total: 1,
        pending: 0,
        planning: 0,
        running: 1,
        done: 0,
        failed: 0,
        hung: 0,
        skipped: 0,
        percent: 0,
      },
      tasks: [
        {
          node_id: 'n-first',
          task_id: 't1',
          sequence: 1,
          status: 'RUNNING',
          ...node,
        },
      ],
      relations: [],
    } as TaskDashboardResponse);

  it('手动切换任务使用第一个步骤节点会话，并携带节点开始时间定位左侧消息', async () => {
    const record = item('t1', 'EXECUTING', '当前任务', { sessionId: 'session-main' });
    mockDashboardTask.mockResolvedValueOnce({
      code: 0,
      message: 'ok',
      data: dashboardWithFirstNode({
        run_info: {
          run_mode: 'single_bot',
          assignee: 'bot-first',
          start_time: 1760000000000,
          end_time: 1760000060000,
          extend_props: { session_id: 'session-first-step' },
        },
      }),
      request_id: 'r',
    });

    await expect(buildTaskConversationNavigationTarget(record)).resolves.toMatchObject({
      taskId: 't1',
      nodeId: 'n-first',
      sessionId: 'session-first-step',
      kind: 'bot',
      groupId: null,
      botId: 'bot-first',
      startTime: 1760000000000,
      endTime: 1760000060000,
    });
  });

  it('协作群第一步骤节点透传群会话标识与群 id', async () => {
    const record = {
      ...item('t1', 'EXECUTING', '群任务', { sessionId: 'session-main' }),
      source_type: 'coop_group' as const,
    };
    mockDashboardTask.mockResolvedValueOnce({
      code: 0,
      message: 'ok',
      data: dashboardWithFirstNode({
        run_info: {
          run_mode: 'coop_group',
          start_time: 1760000000000,
          extend_props: {
            session_id: 'bcs_grp_group-1:round:1',
            group_id: 'group-1',
          },
        },
      }),
      request_id: 'r',
    });

    await expect(buildTaskConversationNavigationTarget(record)).resolves.toMatchObject({
      sessionId: 'bcs_grp_group-1:round:1',
      kind: 'group',
      groupId: 'group-1',
      botId: null,
    });
  });

  it('任务详情失败时不退回整任务时间窗盲跳转', async () => {
    mockDashboardTask.mockRejectedValueOnce(new Error('dashboard failed'));

    await expect(buildTaskConversationNavigationTarget(item('t1', 'EXECUTING', '当前任务', {}))).resolves.toBeNull();
  });
});
