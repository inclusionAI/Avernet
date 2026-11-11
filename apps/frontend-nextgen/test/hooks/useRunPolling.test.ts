/** @jest-environment jsdom */
import { act, renderHook } from '@testing-library/react';
import { useRunPolling } from '@/hooks/useRunPolling';
import { AgentswarmRequestError } from '@/services/agentswarm/http';
import { getRun } from '@/services/agentswarm/service';

jest.mock('@/services/agentswarm/service', () => ({
  getRun: jest.fn(),
  getRunDetail: jest.fn(),
  listRuns: jest.fn(),
  cancelRun: jest.fn(),
}));

const fetchRun = getRun as jest.Mock;
const summary = (status: string) => ({ run_id: 'r1', status });
const flush = async () => {
  await act(async () => {
    await Promise.resolve();
  });
};

beforeEach(() => {
  jest.useFakeTimers();
  fetchRun.mockReset();
});

afterEach(() => {
  jest.useRealTimers();
});

test('polling stops as soon as the run reaches a terminal state', async () => {
  fetchRun.mockResolvedValueOnce(summary('RUNNING')).mockResolvedValueOnce(summary('COMPLETED'));
  const { result } = renderHook(() => useRunPolling({ tenant: 'acme', runId: 'r1', intervalMs: 1000 }));

  await flush();
  expect(result.current.run?.status).toBe('RUNNING');
  expect(result.current.phase).toBe('polling');

  await act(async () => {
    jest.advanceTimersByTime(1000);
    await Promise.resolve();
  });
  expect(result.current.run?.status).toBe('COMPLETED');
  expect(result.current.phase).toBe('stopped');

  // No further polling once stopped.
  const callsAtStop = fetchRun.mock.calls.length;
  await act(async () => {
    jest.advanceTimersByTime(5000);
    await Promise.resolve();
  });
  expect(fetchRun.mock.calls.length).toBe(callsAtStop);
});

test.each(['COMPLETED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'GUARD_KILLED', 'STUCK'])(
  'a %s run stops observation',
  async (status) => {
    fetchRun.mockResolvedValueOnce(summary(status));
    const { result } = renderHook(() => useRunPolling({ tenant: 'acme', runId: 'r1', intervalMs: 1000 }));
    await flush();
    expect(result.current.phase).toBe('stopped');
  },
);

test('a transient read failure keeps the last known state and retries', async () => {
  fetchRun
    .mockResolvedValueOnce(summary('RUNNING'))
    .mockRejectedValueOnce(new AgentswarmRequestError('网关错误', 'unconfirmed', 502))
    .mockResolvedValueOnce(summary('RUNNING'));

  const { result } = renderHook(() => useRunPolling({ tenant: 'acme', runId: 'r1', intervalMs: 1000 }));
  await flush();
  expect(result.current.run?.status).toBe('RUNNING');

  await act(async () => {
    jest.advanceTimersByTime(1000);
    await Promise.resolve();
  });
  // Degraded, but the run is not reported failed and polling continues.
  expect(result.current.degraded).toBe(true);
  expect(result.current.error).toBeNull();
  expect(result.current.phase).toBe('polling');
  expect(result.current.run?.status).toBe('RUNNING');

  await act(async () => {
    jest.advanceTimersByTime(1000);
    await Promise.resolve();
  });
  expect(result.current.degraded).toBe(false);
});

test('a not-found run stops observation with an explanation', async () => {
  fetchRun.mockRejectedValue(new AgentswarmRequestError('run not found', 'not_found', 404));
  const { result } = renderHook(() => useRunPolling({ tenant: 'acme', runId: 'r1', intervalMs: 1000 }));
  await flush();
  expect(result.current.phase).toBe('stopped');
  expect(result.current.error).toContain('不存在');
});

test('no request is made without a tenant or a run id', async () => {
  renderHook(() => useRunPolling({ tenant: undefined, runId: 'r1' }));
  renderHook(() => useRunPolling({ tenant: 'acme', runId: undefined }));
  await flush();
  expect(fetchRun).not.toHaveBeenCalled();
});

test('disabled observation makes no request', async () => {
  renderHook(() => useRunPolling({ tenant: 'acme', runId: 'r1', enabled: false }));
  await flush();
  expect(fetchRun).not.toHaveBeenCalled();
});