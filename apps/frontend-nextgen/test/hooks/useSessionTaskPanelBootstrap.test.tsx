/** @jest-environment jsdom */
import {
  buildRelayTaskPanelTab,
  buildSessionTaskPanelTab,
  findLatestTaskForSession,
  resolveSessionOwnerUserId,
  useSessionTaskPanelBootstrap,
} from '@/hooks/useSessionTaskPanelBootstrap';
import type { TaskListItem } from '@/domain/tasks/models';
import { useTaskSessionAssociationStore } from '@/stores/taskSessionAssociationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { PanelHandle } from '@tc-chat/core';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';

const mockGetTaskBySession = jest.fn();
const mockListMyTasks = jest.fn();

jest.mock('@/services/myTask', () => ({
  getTaskBySession: (...args: unknown[]) => mockGetTaskBySession(...args),
  isEnvelopeFailure: (result?: { code?: number }) => result?.code !== 0,
  listMyTasks: (...args: unknown[]) => mockListMyTasks(...args),
  normalizeMyTaskPage: (data: unknown) => ({
    items: Array.isArray(data) ? data : [],
    total: 0,
    page: 1,
    pageSize: 10,
  }),
}));

const sessionRef = (taskId: string, nodeId = `node-${taskId}`) => ({
  task_id: taskId,
  owner_user_id: 'u1',
  node_id: nodeId,
  updated_at: '2026-11-05T10:00:00Z',
});

const taskRecord = (
  taskId: string,
  modified: string,
  sessionId = 'sess-1',
): TaskListItem =>
  ({
    id: 1,
    task_id: taskId,
    source_type: 'bot',
    owner_user_id: 'u1',
    owner_bot_id: 'b1',
    execution_config: { task_type: 'dynamic', main_session_id: sessionId },
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

it('历史未就绪时不请求任务，历史就绪后保留原恢复流程', async () => {
  const panel = makePanelRef();
  const { rerender } = renderHook(({ ready }) => useSessionTaskPanelBootstrap({
    sessionId: 'sess-1', userId: 'u1', panelRef: panel.ref, enabled: ready,
  }), { initialProps: { ready: false } });
  expect(mockGetTaskBySession).not.toHaveBeenCalled();
  expect(mockListMyTasks).not.toHaveBeenCalled();
  rerender({ ready: true });
  await waitFor(() => expect(mockGetTaskBySession).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(mockListMyTasks).toHaveBeenCalledTimes(1));
});

beforeEach(() => {
  jest.clearAllMocks();
  mockGetTaskBySession.mockResolvedValue({ ok: true, data: null });
  mockListMyTasks.mockResolvedValue({ code: 0, data: [] });
  localStorage.clear();
  useTaskSidePanelStore.getState().resetAll();
  useTaskSessionAssociationStore.getState().reset();
});

describe('findLatestTaskForSession', () => {
  it('只选择当前会话中最后更新的任务', () => {
    const older = taskRecord('task-old', '2026-10-27 10:00:00');
    const newer = taskRecord('task-new', '2026-10-27 11:30:00');
    const otherSession = taskRecord('task-other', '2026-10-27 12:00:00', 'sess-2');

    expect(findLatestTaskForSession([older, newer, otherSession], 'sess-1')?.task_id).toBe('task-new');
  });
});

describe('resolveSessionOwnerUserId', () => {
  it('优先解析 session_id 中的会话归属人，缺失时回退当前登录人', () => {
    expect(resolveSessionOwnerUserId('agent:main:session:abc:user:440718', '146836')).toBe('440718');
    expect(resolveSessionOwnerUserId('sess-1', '146836')).toBe('146836');
    expect(resolveSessionOwnerUserId(null, '146836')).toBe('146836');
  });
});

describe('buildSessionTaskPanelTab', () => {
  it('构造幂等 task tab 和任务面板参数', () => {
    const tab = buildSessionTaskPanelTab(taskRecord('task-1', '2026-10-27 11:30:00'), 'u1');

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
    const tab = buildRelayTaskPanelTab('task-relay', 'u1', '接力任务');

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
      userId: 'u1',
      taskApiBase: '/openapi/v1/collaboration/tasks',
    });
  });
});

describe('useSessionTaskPanelBootstrap', () => {
  it('by_session 返回多个 task 时默认打开最新 ref，并记录全部关联 task', async () => {
    const { ref, openTab } = makePanelRef();
    mockGetTaskBySession.mockResolvedValue({
      ok: true,
      data: [sessionRef('task-new'), sessionRef('task-old')],
    });
    mockListMyTasks.mockResolvedValue({
      code: 0,
      data: [taskRecord('task-old', '2026-10-27 10:00:00'), taskRecord('task-new', '2026-10-27 11:30:00')],
    });

    renderHook(() =>
      useSessionTaskPanelBootstrap({ sessionId: 'sess-1', userId: 'u1', panelRef: ref }),
    );

    await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
    expect(mockGetTaskBySession).toHaveBeenCalledWith('sess-1');
    expect(mockListMyTasks).toHaveBeenCalledWith({ user_id: 'u1' });
    expect(openTab.mock.calls[0][0]).toMatchObject({
      id: 'task-task-new',
      title: 'task-new',
      type: 'taskPanel.TaskLoopView',
    });
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('task-new');
    expect(useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId).toBeNull();
    expect(useTaskSessionAssociationStore.getState().activeSessionTaskIds).toEqual([
      'task-new',
      'task-old',
    ]);
  });

  it('前一个会话残留的焦点任务不会阻止新会话自动恢复', async () => {
    const { ref, openTab } = makePanelRef();

    useTaskSessionAssociationStore.getState().setActiveSession('sess-old');
    useTaskSessionAssociationStore.getState().setActiveSessionTaskIds('sess-old', ['task-old']);
    useTaskSidePanelStore.getState().setActiveTask('task-old');

    mockGetTaskBySession.mockResolvedValue({
      ok: true,
      data: [sessionRef('task-new')],
    });
    mockListMyTasks.mockResolvedValue({ code: 0, data: [] });

    renderHook(() =>
      useSessionTaskPanelBootstrap({ sessionId: 'sess-new', userId: 'u1', panelRef: ref }),
    );

    await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
    expect(openTab.mock.calls[0][0]).toMatchObject({ id: 'task-task-new' });
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('task-new');
  });

  it('任务列表失败但 by_session 命中时仍恢复最新 task', async () => {
    const { ref, openTab } = makePanelRef();
    mockGetTaskBySession.mockResolvedValue({
      ok: true,
      data: [sessionRef('task-relay')],
    });
    mockListMyTasks.mockResolvedValue({ code: 500000, message: 'failed', data: null });

    renderHook(() =>
      useSessionTaskPanelBootstrap({ sessionId: 'sess-relay', userId: 'u1', panelRef: ref }),
    );

    await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
    expect(openTab.mock.calls[0][0]).toMatchObject({
      id: 'task-task-relay',
      title: '任务',
      type: 'taskPanel.TaskLoopView',
    });
    expect(openTab.mock.calls[0][0].params).toMatchObject({
      taskId: 'task-relay',
      userId: 'u1',
      taskApiBase: '/openapi/v1/collaboration/tasks',
    });
  });

  it('by_session 命中先于 PanelHandle 就绪时，短暂等待后仍自动拉起副屏', async () => {
    jest.useFakeTimers();
    try {
      const { ref: readyRef, openTab } = makePanelRef();
      const nullRef: React.RefObject<PanelHandle | null> = { current: null };
      mockGetTaskBySession.mockResolvedValue({
        ok: true,
        data: [sessionRef('task-relay')],
      });

      renderHook(() =>
        useSessionTaskPanelBootstrap({ sessionId: 'sess-relay', userId: 'u1', panelRef: nullRef }),
      );
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

  it('深链冷启动时 PanelHandle 晚于 2 秒仍可自动拉起副屏', async () => {
    jest.useFakeTimers();
    try {
      const { ref: readyRef, openTab } = makePanelRef();
      const nullRef: React.RefObject<PanelHandle | null> = { current: null };
      mockGetTaskBySession.mockResolvedValue({
        ok: true,
        data: [sessionRef('task-relay')],
      });

      renderHook(() =>
        useSessionTaskPanelBootstrap({ sessionId: 'sess-relay', userId: 'u1', panelRef: nullRef }),
      );
      await act(async () => Promise.resolve());
      await act(async () => jest.advanceTimersByTimeAsync(2000));
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

  it('任务列表进入对话时优先打开用户点击的任务，而不是会话最新任务', async () => {
    const { ref, openTab } = makePanelRef();
    useTaskSidePanelStore.getState().setActiveTask('task-current');
    useTaskSidePanelStore.getState().markTaskPanelOpenIntent('task-old');
    mockGetTaskBySession.mockResolvedValue({
      ok: true,
      data: [sessionRef('task-new'), sessionRef('task-old')],
    });
    mockListMyTasks.mockResolvedValue({
      code: 0,
      data: [taskRecord('task-old', '2026-10-27 10:00:00'), taskRecord('task-new', '2026-10-27 11:30:00')],
    });

    renderHook(() =>
      useSessionTaskPanelBootstrap({ sessionId: 'sess-1', userId: 'u1', panelRef: ref }),
    );

    await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
    expect(openTab.mock.calls[0][0]).toMatchObject({ id: 'task-task-old' });
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('task-old');
    expect(useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId).toBeNull();
  });

  it('AixUI 已展示任务时，by_session 命中不同或相同 task 都不抢焦点', async () => {
    const { ref, openTab } = makePanelRef();
    type SessionRef = ReturnType<typeof sessionRef>;
    let resolveSessionTask: (value: { ok: true; data: SessionRef[] | null }) => void = () => undefined;
    mockGetTaskBySession.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSessionTask = resolve;
        }),
    );

    renderHook(() =>
      useSessionTaskPanelBootstrap({ sessionId: 'sess-1', userId: 'u1', panelRef: ref }),
    );

    await act(async () => Promise.resolve());
    act(() => {
      useTaskSidePanelStore.getState().setActiveTask('task-aixui');
    });
    await act(async () => {
      resolveSessionTask({ ok: true, data: [sessionRef('task-relay')] });
    });

    expect(openTab).not.toHaveBeenCalled();
    expect(mockListMyTasks).not.toHaveBeenCalled();
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('task-aixui');
  });

  it('by_session 未命中时按任务列表选择当前会话最新任务', async () => {
    const { ref, openTab } = makePanelRef();
    const sessionId = 'agent:main:session:sess-1:user:440718';
    const ownerTask = taskRecord('task-owner', '2026-10-27 11:30:00', sessionId);
    const viewerTask = taskRecord('task-viewer', '2026-10-27 10:30:00', 'sess-other');
    mockGetTaskBySession.mockResolvedValue({ ok: true, data: [] });
    mockListMyTasks.mockResolvedValue({ code: 0, data: [viewerTask, ownerTask] });

    renderHook(() =>
      useSessionTaskPanelBootstrap({ sessionId, userId: '146836', panelRef: ref }),
    );

    await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
    expect(mockListMyTasks).toHaveBeenCalledWith({ user_id: '440718' });
    expect(openTab.mock.calls[0][0]).toMatchObject({ id: 'task-task-owner' });
    expect(openTab.mock.calls[0][0].params).toMatchObject({ userId: '146836' });
  });

  it('同一 session bootstrap 只执行一次', async () => {
    const { ref, openTab } = makePanelRef();
    mockListMyTasks.mockResolvedValue({
      code: 0,
      data: [taskRecord('task-new', '2026-10-27 11:30:00')],
    });

    const { rerender } = renderHook(
      ({ sessionId }: { sessionId: string | null }) =>
        useSessionTaskPanelBootstrap({ sessionId, userId: 'u1', panelRef: ref }),
      { initialProps: { sessionId: 'sess-1' } },
    );

    await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
    rerender({ sessionId: 'sess-1' });
    await act(async () => Promise.resolve());
    expect(mockListMyTasks).toHaveBeenCalledTimes(1);
    expect(openTab).toHaveBeenCalledTimes(1);
  });

  it('群列表重拉使会话短暂为空后，重新进入同一 session 仍恢复任务', async () => {
    const { ref, openTab } = makePanelRef();
    let resolveFirst: (value: unknown) => void = () => undefined;
    mockGetTaskBySession
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValue({ ok: true, data: [sessionRef('task-new')] });

    const { rerender } = renderHook(
      ({ sessionId }: { sessionId: string | null }) =>
        useSessionTaskPanelBootstrap({ sessionId, userId: 'u1', panelRef: ref }),
      { initialProps: { sessionId: 'sess-1' as string | null } },
    );
    expect(mockGetTaskBySession).toHaveBeenCalledTimes(1);

    rerender({ sessionId: null });
    // 旧请求即使返回也不能打开已离开的会话。
    await act(async () => { resolveFirst({ ok: true, data: [sessionRef('task-old')] }); });
    expect(openTab).not.toHaveBeenCalled();

    rerender({ sessionId: 'sess-1' });
    await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
    expect(mockGetTaskBySession).toHaveBeenCalledTimes(2);
    expect(openTab.mock.calls[0][0]).toMatchObject({ id: 'task-task-new' });
  });

  it('StrictMode 取消首次 effect 后仍能恢复当前会话的任务', async () => {
    const { ref, openTab } = makePanelRef();
    mockGetTaskBySession.mockResolvedValue({ ok: true, data: [sessionRef('task-new')] });

    renderHook(
      () => useSessionTaskPanelBootstrap({ sessionId: 'sess-1', userId: 'u1', panelRef: ref }),
      { wrapper: StrictMode },
    );

    await waitFor(() => expect(openTab).toHaveBeenCalledTimes(1));
    expect(openTab.mock.calls[0][0]).toMatchObject({ id: 'task-task-new' });
  });

  it.each(['任务列表兜底', '显式任务入口'])('%s 命中先于群面板挂载时仍等待 handle 恢复任务', async (source) => {
    jest.useFakeTimers();
    try {
      const { ref: readyRef, openTab } = makePanelRef();
      const panelRef = { current: null as PanelHandle | null };
      mockListMyTasks.mockResolvedValue({
        code: 0,
        data: [taskRecord('task-new', '2026-10-27 11:30:00')],
      });
      if (source === '显式任务入口') {
        useTaskSidePanelStore.getState().markTaskPanelOpenIntent('task-new');
      }

      renderHook(() =>
        useSessionTaskPanelBootstrap({ sessionId: 'sess-1', userId: 'u1', panelRef }),
      );
      await act(async () => Promise.resolve());
      expect(openTab).not.toHaveBeenCalled();

      panelRef.current = readyRef.current;
      await act(async () => jest.advanceTimersByTimeAsync(100));
      expect(openTab).toHaveBeenCalledTimes(1);
      expect(openTab.mock.calls[0][0]).toMatchObject({ id: 'task-task-new' });
    } finally {
      jest.useRealTimers();
    }
  });

  it.each(['关联任务', '任务列表兜底', '显式任务入口'])('%s 等待面板期间离开页面后不会打开旧任务', async (source) => {
    jest.useFakeTimers();
    try {
      const { ref: readyRef, openTab } = makePanelRef();
      const panelRef = { current: null as PanelHandle | null };
      if (source === '关联任务') {
        mockGetTaskBySession.mockResolvedValue({ ok: true, data: [sessionRef('task-old')] });
      }
      mockListMyTasks.mockResolvedValue({
        code: 0,
        data: [taskRecord('task-old', '2026-10-27 11:30:00')],
      });
      if (source === '显式任务入口') {
        useTaskSidePanelStore.getState().markTaskPanelOpenIntent('task-old');
      }
      const { unmount } = renderHook(() =>
        useSessionTaskPanelBootstrap({ sessionId: 'sess-1', userId: 'u1', panelRef }),
      );
      await act(async () => Promise.resolve());
      expect(jest.getTimerCount()).toBe(1);

      unmount();
      panelRef.current = readyRef.current;
      await act(async () => jest.advanceTimersByTimeAsync(100));

      expect(openTab).not.toHaveBeenCalled();
      expect(useTaskSidePanelStore.getState().activeTaskId).toBeNull();
      expect(jest.getTimerCount()).toBe(0);
    } finally {
      jest.useRealTimers();
    }
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
    expect(useTaskSessionAssociationStore.getState().activeSessionTaskIds).toEqual([]);
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
