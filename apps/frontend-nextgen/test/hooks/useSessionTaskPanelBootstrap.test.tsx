/** @jest-environment jsdom */
import {
  buildRelayTaskPanelTab,
  buildSessionTaskPanelTab,
  findLatestTaskForSession,
  useSessionTaskPanelBootstrap,
} from '@/hooks/useSessionTaskPanelBootstrap';
import type { TaskListItem } from '@/domain/tasks/models';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { PanelHandle } from '@tc-chat/core';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

const mockGetTaskBySession = jest.fn();
const mockListMyTasks = jest.fn();

jest.mock('@/services/myTask', () => ({
  getTaskBySession: (...args: unknown[]) => mockGetTaskBySession(...args),
  isEnvelopeFailure: (result?: { code?: number }) => result?.code !== 0,
  listMyTasks: (...args: unknown[]) => mockListMyTasks(...args),
  normalizeMyTaskPage: (data: unknown) => ({ items: Array.isArray(data) ? data : [], total: 0, page: 1, pageSize: 10 }),
}));

const taskRecord = (taskId: string, modified: string): TaskListItem =>
  ({
    id: 1,
    task_id: taskId,
    source_type: 'bot',
    owner_user_id: 'u1',
    owner_bot_id: 'b1',
    execution_config: { task_type: 'dynamic', main_session_id: 'sess-1' },
    task_spec: {
      context: { title: taskId, background: '', extend_props: {} },
      goal: { objective: '', acceptances: [] },
    },
    status: 'EXECUTING',
    gmt_create: '2026-10-27 10:00:00',
    gmt_modified: modified,
  }) as TaskListItem;

const makePanelRef = () => {
  const openTab = jest.fn();
  const handle = { openTab, closePanel: jest.fn() } as unknown as PanelHandle;
  return { openTab, ref: { current: handle } as React.RefObject<PanelHandle | null> };
};

beforeEach(() => {
  jest.clearAllMocks();
  mockGetTaskBySession.mockResolvedValue({ ok: true, data: null });
  localStorage.clear();
  useTaskSidePanelStore.getState().resetAll();
});

describe('findLatestTaskForSession', () => {
  it('只选择当前会话中最后更新的任务', () => {
    const older = taskRecord('task-old', '2026-10-27 10:00:00');
    const newer = taskRecord('task-new', '2026-10-27 11:30:00');
    const otherSession = { ...newer, task_id: 'task-other', execution_config: { task_type: 'dynamic', main_session_id: 'sess-2' } } as TaskListItem;

    expect(findLatestTaskForSession([older, newer, otherSession], 'sess-1')?.task_id).toBe('task-new');
  });
});

describe('buildSessionTaskPanelTab', () => {
  it('构造幂等 task tab 和任务面板参数', () => {
    const record = taskRecord('task-1', '2026-10-27 11:30:00');
    const tab = buildSessionTaskPanelTab(record, 'u1');

    expect(tab).toMatchObject({
      id: 'task-task-1',
      title: 'task-1',
      type: 'taskPanel.TaskLoopView',
      closable: true,
      defaultWidth: 420,
    });
    expect(tab.params).toMatchObject({ taskId: 'task-1', apiBaseUrl: '', bcsBaseUrl: '', userId: 'u1' });
  });
});

describe('buildRelayTaskPanelTab', () => {
  it('构造第二棒整图 TaskPanel tab', () => {
    const tab = buildRelayTaskPanelTab('task-relay', 'owner-1', '接力任务');

    expect(tab).toMatchObject({
      id: 'task-task-relay',
      title: '接力任务',
      type: 'taskPanel.TaskLoopView',
      closable: true,
      defaultWidth: 420,
    });
    expect(tab.params).toMatchObject({
      taskId: 'task-relay',
      apiBaseUrl: '',
      bcsBaseUrl: '',
      userId: 'owner-1',
      taskApiBase: '/openapi/v1/collaboration/tasks',
    });
  });
});

describe('useSessionTaskPanelBootstrap', () => {
  it('第二棒会话命中后优先打开该任务，不查询任务列表', async () => {
    const { ref, openTab } = makePanelRef();
    mockGetTaskBySession.mockResolvedValue({
      ok: true,
      data: {
        task_id: 'task-relay',
        owner_user_id: 'owner-relay',
        node_id: 'node-relay',
      },
    });

    renderHook(() =>
      useSessionTaskPanelBootstrap({ sessionId: 'sess-relay', userId: 'u1', panelRef: ref }),
    );

    await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
    expect(mockGetTaskBySession).toHaveBeenCalledWith('sess-relay');
    expect(mockListMyTasks).not.toHaveBeenCalled();
    expect(openTab.mock.calls[0][0]).toMatchObject({
      id: 'task-task-relay',
      type: 'taskPanel.TaskLoopView',
    });
    expect(openTab.mock.calls[0][0].params).toMatchObject({
      taskId: 'task-relay',
      userId: 'owner-relay',
      taskApiBase: '/openapi/v1/collaboration/tasks',
    });
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('task-relay');
    expect(useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId).toBeNull();
  });

  it('第二棒命中先于 PanelHandle 就绪时，短暂等待后仍自动拉起副屏', async () => {
    jest.useFakeTimers();
    try {
      const { ref: readyRef, openTab } = makePanelRef();
      const nullRef: React.RefObject<PanelHandle | null> = { current: null };
      mockGetTaskBySession.mockResolvedValue({
        ok: true,
        data: { task_id: 'task-relay', owner_user_id: 'owner-relay', node_id: 'node-relay' },
      });

      renderHook(() =>
        useSessionTaskPanelBootstrap({ sessionId: 'sess-relay', userId: 'u1', panelRef: nullRef }),
      );
      // by_session 已返回，但深链持久副屏 owner 仍在同帧后的 React commit 中接管 ref。
      await act(async () => Promise.resolve());
      expect(openTab).not.toHaveBeenCalled();

      (nullRef as { current: PanelHandle | null }).current = readyRef.current;
      await act(async () => jest.advanceTimersByTimeAsync(100));

      expect(openTab).toHaveBeenCalledTimes(1);
      expect(openTab.mock.calls[0][0]).toMatchObject({
        id: 'task-task-relay',
        type: 'taskPanel.TaskLoopView',
      });
    } finally {
      jest.useRealTimers();
    }
  });

  it('进入会话即按任务记录打开最新 TaskPanel，不依赖历史消息分页', async () => {
    const { ref, openTab } = makePanelRef();
    mockListMyTasks.mockResolvedValue({
      code: 0,
      data: [
        taskRecord('task-old', '2026-10-27 10:00:00'),
        taskRecord('task-new', '2026-10-27 11:30:00'),
      ],
    });

    const { rerender } = renderHook(
      ({ sessionId }: { sessionId: string | null }) =>
        useSessionTaskPanelBootstrap({ sessionId, userId: 'u1', panelRef: ref }),
      { initialProps: { sessionId: 'sess-1' } },
    );

    await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
    expect(mockListMyTasks).toHaveBeenCalledWith({ user_id: 'u1' });
    expect(openTab.mock.calls[0][0]).toMatchObject({ id: 'task-task-new' });
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('task-new');

    rerender({ sessionId: 'sess-1' });
    await act(async () => Promise.resolve());
    expect(mockListMyTasks).toHaveBeenCalledTimes(1);
    expect(openTab).toHaveBeenCalledTimes(1);
  });

  it('任务列表进入对话时优先打开用户点击的任务，而不是会话最新任务', async () => {
    const { ref, openTab } = makePanelRef();
    useTaskSidePanelStore.getState().setActiveTask('task-current');
    useTaskSidePanelStore.getState().markTaskPanelOpenIntent('task-old');
    mockListMyTasks.mockResolvedValue({
      code: 0,
      data: [
        taskRecord('task-old', '2026-10-27 10:00:00'),
        taskRecord('task-new', '2026-10-27 11:30:00'),
      ],
    });

    renderHook(() =>
      useSessionTaskPanelBootstrap({ sessionId: 'sess-1', userId: 'u1', panelRef: ref }),
    );

    await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
    expect(openTab.mock.calls[0][0]).toMatchObject({ id: 'task-task-old' });
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('task-old');
    expect(useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId).toBeNull();
  });

  it('已有活跃任务面板时，返回原会话不重新 bootstrap 当前副屏', async () => {
    const { ref, openTab } = makePanelRef();
    useTaskSidePanelStore.getState().setActiveTask('task-current');
    mockListMyTasks.mockResolvedValue({
      code: 0,
      data: [taskRecord('task-new', '2026-10-27 11:30:00')],
    });

    renderHook(() =>
      useSessionTaskPanelBootstrap({ sessionId: 'sess-1', userId: 'u1', panelRef: ref }),
    );
    await act(async () => Promise.resolve());

    expect(mockListMyTasks).not.toHaveBeenCalled();
    expect(openTab).not.toHaveBeenCalled();
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('task-current');
  });

  it('没有命中当前会话的任务时不打开副屏', async () => {
    const { ref, openTab } = makePanelRef();
    mockListMyTasks.mockResolvedValue({ code: 0, data: [taskRecord('task-old', '2026-10-27 10:00:00')] });

    renderHook(() =>
      useSessionTaskPanelBootstrap({ sessionId: 'sess-other', userId: 'u1', panelRef: ref }),
    );

    await act(async () => Promise.resolve());
    expect(openTab).not.toHaveBeenCalled();
    expect(useTaskSidePanelStore.getState().activeTaskId).toBeNull();
  });

  it('任务列表失败时静默不打扰会话', async () => {
    const { ref, openTab } = makePanelRef();
    mockListMyTasks.mockRejectedValue(new Error('offline'));

    renderHook(() =>
      useSessionTaskPanelBootstrap({ sessionId: 'sess-1', userId: 'u1', panelRef: ref }),
    );

    await act(async () => Promise.resolve());
    expect(openTab).not.toHaveBeenCalled();
    expect(useTaskSidePanelStore.getState().activeTaskId).toBeNull();
  });
});
