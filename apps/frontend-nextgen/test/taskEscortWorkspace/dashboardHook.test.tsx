/** @jest-environment jsdom */
import { useTaskEscortDashboard } from '@/hooks/taskEscortWorkspace/useTaskEscortDashboard';
import { dashboardService } from '@/services/taskEscortWorkspace/dashboardService';
import { act, renderHook, waitFor } from '@testing-library/react';
jest.mock('@/services/taskEscortWorkspace/dashboardService', () => ({
  dashboardService: {
    overview: jest.fn(),
    dailyTrend: jest.fn(),
    failureHotspots: jest.fn(),
    workflowHealth: jest.fn(),
    workflowReleaseStats: jest.fn(),
  },
  isDashboardForbidden: (error: any) => error?.status === 403,
}));
const api = jest.mocked(dashboardService);
const overview = { totalRuns: 1234, successRate: 0.875, failedCount: 9, statusDistribution: {} };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
beforeEach(() => {
  jest.resetAllMocks();
  window.history.replaceState({}, '', '/?demo=1&workflowId=must-ignore');
  api.overview.mockResolvedValue(overview as any);
  api.dailyTrend.mockResolvedValue({ from: 1, to: 2, dates: [] });
  api.failureHotspots.mockResolvedValue({ from: 1, to: 2, hotspots: [] });
  api.workflowHealth.mockResolvedValue({ available: true, from: 1, to: 2, workflows: [] });
  api.workflowReleaseStats.mockResolvedValue({ available: false, workflows: [] });
});
it('gates identity readiness then calls only the five real global sources, even for legacy demo URL', async () => {
  const { result, rerender } = renderHook(({ ready }) => useTaskEscortDashboard({ key: 'u:s', ready }), {
    initialProps: { ready: false },
  });
  expect(api.overview).not.toHaveBeenCalled();
  rerender({ ready: true });
  await waitFor(() => expect(result.current.overview.data?.totalRuns).toBe(1234));
  expect(result.current.range).toBe('7d');
  Object.values(api).forEach((method) => {
    expect(method).toHaveBeenCalledTimes(1);
    const [range, signal] = method.mock.calls[0];
    expect(Object.keys(range).sort()).toEqual(['from', 'to']);
    expect(range.to - range.from).toBe(7 * 86400);
    expect(signal).toBeInstanceOf(AbortSignal);
  });
});
it('changes only independent widget requests, and propagates global range and refresh', async () => {
  const { result, rerender } = renderHook(({ period }) => useTaskEscortDashboard({ key: 'u:s', ready: true }, period), {
    initialProps: { period: '7d' as '7d' | 'today' },
  });
  await waitFor(() => expect(result.current.overview.data).not.toBeNull());
  act(() => result.current.setWidgetRange('trend', 'yesterday'));
  await waitFor(() => expect(api.dailyTrend).toHaveBeenCalledTimes(2));
  expect(api.overview).toHaveBeenCalledTimes(1);
  expect(api.failureHotspots).toHaveBeenCalledTimes(1);
  rerender({ period: 'today' });
  await waitFor(() => expect(api.overview).toHaveBeenCalledTimes(2));
  expect(api.dailyTrend).toHaveBeenCalledTimes(2);
  act(() => result.current.refresh());
  await waitFor(() => expect(api.dailyTrend).toHaveBeenCalledTimes(3));
  expect(api.workflowReleaseStats).toHaveBeenCalledTimes(3);
});
it('preserves useful partial widgets, but clears failed widget data during retry and on failure', async () => {
  api.dailyTrend.mockRejectedValue(new Error('trend offline'));
  const { result } = renderHook(() => useTaskEscortDashboard({ key: 'u:s', ready: true }));
  await waitFor(() => expect(result.current.trend.error).toBe('trend offline'));
  expect(result.current.overview.data?.totalRuns).toBe(1234);
  expect(result.current.trend.data).toBeNull();
  api.overview.mockRejectedValueOnce(new Error('overview offline'));
  act(() => result.current.overview.retry());
  expect(result.current.overview.data).toBeNull();
  await waitFor(() => expect(result.current.overview.error).toBe('overview offline'));
});
it('clears all widgets on an admin denial and ignores already inflight successful replies', async () => {
  const pending = deferred<any>();
  api.overview.mockReturnValue(pending.promise);
  api.dailyTrend.mockRejectedValue(Object.assign(new Error('只有管理员可以查看全局数据大盘'), { status: 403 }));
  const { result } = renderHook(() => useTaskEscortDashboard({ key: 'u:s', ready: true }));
  await waitFor(() => expect(result.current.forbidden).toBe(true));
  await act(async () => pending.resolve(overview));
  expect(result.current.overview.data).toBeNull();
  expect(result.current.health.data).toBeNull();
});
it('clears immediately across identity/readiness changes and rejects stale identity replies', async () => {
  const pending = deferred<any>();
  api.overview.mockReturnValueOnce(pending.promise);
  const { result, rerender } = renderHook(({ key, ready }) => useTaskEscortDashboard({ key, ready }), {
    initialProps: { key: 'old', ready: true },
  });
  const oldSignal = api.overview.mock.calls[0][1];
  rerender({ key: 'new', ready: false });
  expect(result.current.overview.data).toBeNull();
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => pending.resolve({ ...overview, totalRuns: 99999 }));
  expect(result.current.overview.data).toBeNull();
  rerender({ key: 'new', ready: true });
  await waitFor(() => expect(result.current.overview.data?.totalRuns).toBe(1234));
});
it('a changed range hides old data immediately and ignores its late reply', async () => {
  const old = deferred<any>();
  api.dailyTrend.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ from: 1, to: 2, dates: [] });
  const { result, rerender } = renderHook(({ period }) => useTaskEscortDashboard({ key: 'u:s', ready: true }, period), {
    initialProps: { period: '7d' as '7d' | 'today' },
  });
  rerender({ period: 'today' });
  expect(result.current.trend.data).toBeNull();
  await waitFor(() => expect(result.current.trend.loading).toBe(false));
  await act(async () => old.resolve({ from: 1, to: 2, dates: [{ date: 'obsolete' }] }));
  expect(result.current.trend.data?.dates).toEqual([]);
});
it('retains other data on widget retry and aborts all requests when unmounted', async () => {
  const { result, unmount } = renderHook(() => useTaskEscortDashboard({ key: 'u:s', ready: true }));
  await waitFor(() => expect(result.current.health.loading).toBe(false));
  act(() => result.current.hotspots.retry());
  await waitFor(() => expect(api.failureHotspots).toHaveBeenCalledTimes(2));
  expect(api.overview).toHaveBeenCalledTimes(1);
  expect(result.current.overview.data?.totalRuns).toBe(1234);
  const activeSignals = Object.values(api).map((method) => method.mock.calls.at(-1)?.[1]);
  unmount();
  activeSignals.forEach((signal) => expect(signal?.aborted).toBe(true));
});
it('switching to another ready identity synchronously hides all previous identity data', async () => {
  const { result, rerender } = renderHook(({ key }) => useTaskEscortDashboard({ key, ready: true }), {
    initialProps: { key: 'first' },
  });
  await waitFor(() => expect(result.current.overview.data?.totalRuns).toBe(1234));
  api.overview.mockReturnValueOnce(new Promise(() => {}));
  rerender({ key: 'second' });
  expect(result.current.overview.data).toBeNull();
  expect(result.current.health.data).toBeNull();
  expect(result.current.overview.loading).toBe(true);
});
it('request scopes and readiness errors never trigger duplicate identity loads or any API request', () => {
  renderHook(() => useTaskEscortDashboard({ key: 'u:s', ready: true, error: 'identity unavailable' }));
  Object.values(api).forEach((method) => expect(method).not.toHaveBeenCalled());
});

it('takes the existing controller global period as a controlled input, including changes and resets', async () => {
  const { result, rerender } = renderHook(({ period }) => useTaskEscortDashboard({ key: 'u:s', ready: true }, period), {
    initialProps: { period: '30d' as 'today' | 'yesterday' | '7d' | '30d' },
  });
  await waitFor(() => expect(result.current.overview.data).not.toBeNull());
  expect(result.current.range).toBe('30d');
  expect(api.overview.mock.calls[0][0].to - api.overview.mock.calls[0][0].from).toBe(30 * 86400);
  rerender({ period: 'yesterday' });
  await waitFor(() => expect(api.overview).toHaveBeenCalledTimes(2));
  expect(result.current.range).toBe('yesterday');
  act(() => result.current.setWidgetRange('trend', '7d'));
  await waitFor(() => expect(api.dailyTrend).toHaveBeenCalledTimes(3));
  expect(api.dailyTrend.mock.calls.at(-1)?.[0].to).toBe(api.overview.mock.calls.at(-1)?.[0].to);
  rerender({ period: '7d' });
  await waitFor(() => expect(result.current.range).toBe('7d'));
});
