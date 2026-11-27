/** @jest-environment jsdom */
import { workspacePages } from '@/domain/taskEscortWorkspace';
import { useTaskEscortWorkspace } from '@/hooks/useTaskEscortWorkspace';
import { taskEscortService } from '@/services/taskEscort/taskEscortService';
import { act, renderHook, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';
import { toast } from 'sonner';

jest.mock('@umijs/max', () => {
  const router = require('history').createMemoryHistory();
  const React = require('react');
  return {
    history: router,
    useLocation: () =>
      React.useSyncExternalStore(
        (notify: () => void) => router.listen(notify),
        () => router.location,
      ),
  };
});
jest.mock('sonner', () => ({ toast: Object.assign(jest.fn(), { success: jest.fn(), error: jest.fn() }) }));
jest.mock('@/services/taskEscort/taskEscortService', () => ({
  taskEscortService: { listWorkflows: jest.fn(), listFlowRuns: jest.fn(), getFlowRun: jest.fn() },
}));
const api = jest.mocked(taskEscortService);
const workflow = { workflowId: 'real-workflow', title: 'Real workflow', packId: null, updatedAt: 0 };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

beforeEach(() => {
  jest.resetAllMocks();
  history.replace('/evolution-factory/task-escort');
  api.listWorkflows.mockResolvedValue([]);
  api.listFlowRuns.mockResolvedValue([]);
});

it.each(['', '?demo=1'])('真实读取失败不切换合成数据，重试可以恢复：%s', async (search) => {
  history.replace(`/evolution-factory/task-escort${search}`);
  api.listWorkflows.mockRejectedValueOnce(new Error('403 Forbidden'));
  const { result } = renderHook(() => useTaskEscortWorkspace('user-a'));
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.mode).toBe('live');
  expect(result.current.error).toBe('403 Forbidden');
  expect(result.current.workflows).toEqual([]);
  expect(result.current.runs).toEqual([]);
  expect(result.current.issues).toEqual([]);
  expect(result.current.experiences).toEqual([]);
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.error).toBeNull());
  expect(api.listWorkflows).toHaveBeenCalledTimes(2);
  expect(result.current.workflows).toEqual([]);
});

it.each(workspacePages)('旧 demo=1 的 %s 页面始终读取真实接口，且不暴露演示开关', async (page) => {
  history.replace(`/evolution-factory/task-escort?demo=1&view=${page}`);
  api.listWorkflows.mockResolvedValue([workflow]);
  const { result } = renderHook(() => useTaskEscortWorkspace('user-a'));
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(api.listWorkflows).toHaveBeenCalledTimes(1);
  expect(api.listFlowRuns).toHaveBeenCalledWith({ limit: 100 });
  expect(result.current.mode).toBe('live');
  expect(result.current.workspaceKey).toBe('user-a:live');
  expect(result.current.workflows.map((item) => item.id)).toEqual(['real-workflow']);
  expect(result.current).not.toHaveProperty('enableDemo');
  expect(result.current).not.toHaveProperty('exitDemo');
  expect(result.current.support.editor.available).toBe(false);
});

it('跨视图保留目标工作流，导航清理旧 demo 并保留宿主参数与 hash，历史返回仍为真实模式', async () => {
  history.replace('/evolution-factory/task-escort?demo=1&view=dashboard&host=desktop#section');
  api.listWorkflows.mockResolvedValue([workflow]);
  const { result } = renderHook(() => useTaskEscortWorkspace('user-a'));
  await waitFor(() => expect(result.current.workflows).toHaveLength(1));
  act(() => {
    result.current.navigate('runs');
    result.current.setWorkflowId('real-workflow');
  });
  expect(result.current.page).toBe('runs');
  expect(result.current.workflowId).toBe('real-workflow');
  expect(new URLSearchParams(history.location.search).has('demo')).toBe(false);
  expect(new URLSearchParams(history.location.search).get('host')).toBe('desktop');
  expect(history.location.hash).toBe('#section');
  act(() => result.current.setPeriod('30d'));
  expect(result.current.workflowId).toBe('real-workflow');
  await act(async () => {
    history.back();
  });
  await waitFor(() => expect(result.current.period).toBe('7d'));
  await act(async () => {
    history.back();
  });
  await waitFor(() => expect(result.current.page).toBe('dashboard'));
  expect(result.current.mode).toBe('live');
  expect(result.current.workflowId).toBe('all');
  expect(api.listWorkflows).toHaveBeenCalledTimes(1);
});

it('仅调整周期也清理旧 demo 参数，不改变真实工作区与筛选', async () => {
  history.replace('/evolution-factory/task-escort?demo=1&host=desktop');
  const { result } = renderHook(() => useTaskEscortWorkspace('user-a'));
  await waitFor(() => expect(result.current.loading).toBe(false));
  act(() => result.current.setQuery('keep-filter'));
  act(() => result.current.setPeriod('today'));
  expect(new URLSearchParams(history.location.search).has('demo')).toBe(false);
  expect(result.current.period).toBe('today');
  expect(result.current.query).toBe('keep-filter');
  expect(api.listWorkflows).toHaveBeenCalledTimes(1);
});

it.each(['user-b:space-a', 'user-a:space-b'])('身份或空间变化隔离状态，旧请求不回填到 %s', async (scope) => {
  history.replace('/evolution-factory/task-escort?demo=1');
  const old = deferred<(typeof workflow)[]>();
  api.listWorkflows.mockReturnValueOnce(old.promise).mockResolvedValueOnce([workflow]);
  const { result, rerender } = renderHook(({ scope }) => useTaskEscortWorkspace(scope), {
    initialProps: { scope: 'user-a:space-a' },
  });
  act(() => result.current.setQuery('old-filter'));
  rerender({ scope });
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => old.resolve([{ ...workflow, workflowId: 'old-user-workflow' }]));
  expect(result.current.workflows.map((item) => item.id)).toEqual(['real-workflow']);
  expect(result.current.mode).toBe('live');
  expect(result.current.query).toBe('');
  expect(result.current.workspaceKey).toBe(`${scope}:live`);
  expect(api.listWorkflows).toHaveBeenCalledTimes(2);
});

it('写入与重置不模拟成功，不改变真实数据、草稿清理版本或发送额外请求', async () => {
  history.replace('/evolution-factory/task-escort?demo=1&view=editor');
  api.listWorkflows.mockResolvedValue([workflow]);
  const { result } = renderHook(() => useTaskEscortWorkspace('user-a'));
  await waitFor(() => expect(result.current.loading).toBe(false));
  const epoch = result.current.resetVersion;
  const workflows = result.current.workflows;
  const settings = result.current.settings;
  act(() => {
    result.current.applySuggestion('i1');
    result.current.verifyIssue('i1');
    result.current.saveExperience('i1');
    result.current.updateSettings({ enabled: true });
    expect(result.current.saveSettings()).toBe(false);
    result.current.reset();
  });
  expect(result.current.resetVersion).toBe(epoch);
  expect(result.current.workflows).toBe(workflows);
  expect(result.current.settings).toBe(settings);
  expect(result.current.settingsDirty).toBe(false);
  expect(result.current.page).toBe('editor');
  expect(toast.error).toHaveBeenCalledTimes(6);
  expect(toast.success).not.toHaveBeenCalled();
  expect(api.listWorkflows).toHaveBeenCalledTimes(1);
  expect(api.listFlowRuns).toHaveBeenCalledTimes(1);
  expect(api.getFlowRun).not.toHaveBeenCalled();
});

it.each(['', '?demo=1'])('身份未就绪时保留加载与错误，重试只恢复身份：%s', async (search) => {
  history.replace(`/evolution-factory/task-escort${search}`);
  const retry = jest.fn();
  const { result, rerender } = renderHook(
    ({ ready, loading }) =>
      useTaskEscortWorkspace('user-a', { ready, loading, error: ready ? undefined : '身份加载失败', retry }),
    { initialProps: { ready: false, loading: true } },
  );
  expect(result.current.mode).toBe('live');
  expect(result.current.loading).toBe(true);
  expect(result.current.error).toBe('身份加载失败');
  expect(result.current.workflows).toEqual([]);
  act(() => result.current.refresh());
  expect(retry).toHaveBeenCalledTimes(1);
  expect(api.listWorkflows).not.toHaveBeenCalled();
  expect(api.listFlowRuns).not.toHaveBeenCalled();
  rerender({ ready: true, loading: false });
  await waitFor(() => expect(api.listWorkflows).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(result.current.error).toBeNull());
});

it('旧 demo URL 在身份就绪丢失后取消在途读取，恢复后可重试', async () => {
  history.replace('/evolution-factory/task-escort?demo=1');
  const old = deferred<(typeof workflow)[]>();
  api.listWorkflows.mockReturnValueOnce(old.promise);
  const { result, rerender } = renderHook(({ ready }) => useTaskEscortWorkspace('user-a', { ready }), {
    initialProps: { ready: true },
  });
  expect(api.listWorkflows).toHaveBeenCalledTimes(1);
  rerender({ ready: false });
  await act(async () => old.resolve([workflow]));
  expect(result.current.workflows).toEqual([]);
  rerender({ ready: true });
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(api.listWorkflows).toHaveBeenCalledTimes(2);
});

it('真实详情在身份或空间切换后取消，不向新工作区回填', async () => {
  const detail = deferred<Awaited<ReturnType<typeof taskEscortService.getFlowRun>>>();
  api.getFlowRun.mockReturnValueOnce(detail.promise);
  const { result, rerender } = renderHook(({ scope }) => useTaskEscortWorkspace(scope), {
    initialProps: { scope: 'user-a:space-a' },
  });
  await waitFor(() => expect(result.current.loading).toBe(false));
  const reading = result.current.loadRunDetail('old-run').catch((error: Error) => error);
  rerender({ scope: 'user-b:space-b' });
  await act(async () => detail.resolve({} as never));
  expect(await reading).toMatchObject({ message: expect.stringMatching(/cancel|取消/i) });
  expect(result.current.selectedRun).toBeNull();
});

it('真实读取为空时拒绝不存在的选择，不伪造问题、运行或工作流', async () => {
  history.replace('/evolution-factory/task-escort?demo=1');
  const { result } = renderHook(() => useTaskEscortWorkspace('user-a'));
  await waitFor(() => expect(result.current.loading).toBe(false));
  act(() => {
    result.current.setSelectedRun('missing-run');
    result.current.setSelectedIssue('missing-issue');
    result.current.setWorkflowId('missing-workflow');
    result.current.notice('只读说明');
  });
  expect(result.current.selectedRun).toBeNull();
  expect(result.current.selectedIssue).toBeNull();
  expect(result.current.workflowId).toBe('all');
  expect(toast).toHaveBeenCalledWith('只读说明');
  act(() => {
    result.current.setSelectedRun(null);
    result.current.setSelectedIssue(null);
    result.current.setWorkflowId('all');
    result.current.navigate('runs');
    result.current.setPeriod('7d');
  });
  expect(api.listWorkflows).toHaveBeenCalledTimes(1);
  expect(history.location.search).toBe('?demo=1');
});

it('身份未就绪且无重试回调时仍不发送读取，不误报错误或加载中', () => {
  history.replace('/evolution-factory/task-escort?demo=1');
  const { result } = renderHook(() => useTaskEscortWorkspace('user-a', { ready: false }));
  act(() => result.current.refresh());
  expect(result.current.error).toBeNull();
  expect(result.current.loading).toBe(false);
  expect(result.current.workflows).toEqual([]);
  expect(api.listWorkflows).not.toHaveBeenCalled();
  expect(api.listFlowRuns).not.toHaveBeenCalled();
});
