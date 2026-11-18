/** @jest-environment jsdom */
import type { OverviewRunDetail } from '@/domain/taskEscortParity/overviewTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortOverview } from '@/hooks/taskEscortParity/useTaskEscortOverview';
import { useTaskEscortRunDetail } from '@/hooks/taskEscortParity/useTaskEscortRunDetail';
import { RunDagView } from '@/pages/TaskEscort/parity/overview/RunDagView';
import { overviewService } from '@/services/taskEscortParity/overviewService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/taskEscortParity/overviewService', () => ({
  overviewService: {
    runs: jest.fn(),
    health: jest.fn(),
    trend: jest.fn(),
    nodeStats: jest.fn(),
    runDetail: jest.fn(),
  },
}));
const api = overviewService as jest.Mocked<typeof overviewService>;
const context: TaskEscortParityContext = {
  scopeKey: 'scope',
  userId: 'alice',
  ready: true,
  workflowId: 'wf',
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
beforeEach(() => jest.clearAllMocks());

it('cancels workflow + identity + run switches and ignores late replies even if transport ignores abort', async () => {
  const first = deferred<OverviewRunDetail>();
  const second = deferred<OverviewRunDetail>();
  api.runDetail.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);
  const { result, rerender } = renderHook(({ context, runId }) => useTaskEscortRunDetail(context, runId), {
    initialProps: { context, runId: 'r1' },
  });
  await waitFor(() => expect(api.runDetail).toHaveBeenCalledTimes(1));
  const oldSignal = api.runDetail.mock.calls[0][3]!;
  rerender({ context: { ...context, workflowId: 'wf2', userId: 'bob', scopeKey: 'new' }, runId: 'r2' });
  expect(result.current.data).toBeNull();
  expect(oldSignal.aborted).toBe(true);
  await waitFor(() =>
    expect(api.runDetail).toHaveBeenCalledWith('wf2', 'r2', { user_id: 'bob' }, expect.any(AbortSignal)),
  );
  await act(async () => second.resolve({ run: { id: 'r2' }, nodes: [] } as unknown as OverviewRunDetail));
  expect(result.current.data?.run.id).toBe('r2');
  await act(async () => first.resolve({ run: { id: 'r1' }, nodes: [] } as unknown as OverviewRunDetail));
  expect(result.current.data?.run.id).toBe('r2');
  rerender({ context: { ...context, ready: false }, runId: 'r2' });
  expect(result.current.data).toBeNull();
});

it('isolates same-workflow identity switch across list, health, trend and metrics and issues no reads while unready', async () => {
  const oldList = deferred<Awaited<ReturnType<typeof api.runs>>>();
  api.runs.mockImplementation((params) =>
    params.user_id === 'alice'
      ? oldList.promise
      : Promise.resolve({ runs: [], total: 222, statusCounts: { succeeded: 222 } }),
  );
  api.health.mockResolvedValue({ overallScore: 90 } as never);
  api.trend.mockResolvedValue([]);
  api.nodeStats.mockResolvedValue({ nodes: [] } as never);
  const filters = { page: 0, status: '', query: '', time: '7d' };
  const { result, rerender } = renderHook(({ context }) => useTaskEscortOverview(context, '7', filters), {
    initialProps: { context: { ...context, ready: false } },
  });
  expect(api.runs).not.toHaveBeenCalled();
  rerender({ context });
  await waitFor(() => expect(api.runs).toHaveBeenCalledTimes(2));
  const signals = api.runs.mock.calls.map((call) => call[1]);
  rerender({ context: { ...context, userId: 'bob' } });
  expect(result.current.runs.data).toBeNull();
  await waitFor(() => expect(result.current.metrics.data?.total).toBe(222));
  expect(signals.every((signal) => signal?.aborted)).toBe(true);
  await act(async () => oldList.resolve({ runs: [], total: 111, statusCounts: { failed: 111 } }));
  expect(result.current.runs.data?.total).toBe(222);
  expect(result.current.metrics.data?.statusCounts).toEqual({ succeeded: 222 });
  expect(api.health).toHaveBeenLastCalledWith('wf', { user_id: 'bob', days: 7 }, expect.any(AbortSignal));
});

it('surfaces 403 and permits an actual retry without falling back to old detail', async () => {
  api.runDetail
    .mockRejectedValueOnce(new Error('403 无权限'))
    .mockResolvedValueOnce({ run: { id: 'real' }, nodes: [] } as unknown as OverviewRunDetail);
  const { result } = renderHook(() => useTaskEscortRunDetail(context, 'real'));
  await waitFor(() => expect(result.current.error).toBe('403 无权限'));
  expect(result.current.data).toBeNull();
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.data?.run.id).toBe('real'));
  expect(result.current.error).toBeNull();
});

it('DAG programmatic Drawer returns Escape focus to its node opener and stays within a 390px viewport', async () => {
  const previousWidth = window.innerWidth;
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
  try {
    render(
      <RunDagView
        workflow={{
          id: 'wf',
          version: null,
          nodes: [{ id: 'node', title: 'Actual node', executor: null, dependsOn: [] }],
        }}
        nodes={[]}
        runVersion={null}
      />,
    );
    const trigger = screen.getByRole('button', { name: 'DAG 节点 Actual node' });
    trigger.focus();
    fireEvent.click(trigger);
    const drawer = await screen.findByRole('dialog');
    fireEvent.keyDown(drawer, { key: 'Escape', code: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
    fireEvent.click(trigger);
    expect(await screen.findByRole('dialog')).toHaveClass('max-w-full');
  } finally {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: previousWidth });
  }
});
