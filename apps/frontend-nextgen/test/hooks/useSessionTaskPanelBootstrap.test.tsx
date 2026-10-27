/** @jest-environment jsdom */
import {
  buildSessionTaskPanelTab,
  findLatestTaskForSession,
  useSessionTaskPanelBootstrap,
} from '@/hooks/useSessionTaskPanelBootstrap';
import type { TaskListItem } from '@/domain/tasks/models';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { PanelHandle } from '@tc-chat/core';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

const mockListMyTasks = jest.fn();

jest.mock('@/services/myTask', () => ({
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

describe('useSessionTaskPanelBootstrap', () => {
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
