/** @jest-environment jsdom */
import { act, renderHook } from '@testing-library/react';
import { useRunList } from '@/hooks/useRunList';
import { AgentswarmRequestError } from '@/services/agentswarm/http';
import { listRuns } from '@/services/agentswarm/service';

jest.mock('@/services/agentswarm/service', () => ({
  getRun: jest.fn(),
  getRunDetail: jest.fn(),
  listRuns: jest.fn(),
  cancelRun: jest.fn(),
}));

const fetchList = listRuns as jest.Mock;
const page = (items: Array<{ run_id: string; status: string }>) => ({ items, total: items.length, page: 1, page_size: 20 });
const flush = async () => {
  await act(async () => {
    await Promise.resolve();
  });
};

beforeEach(() => {
  jest.useFakeTimers();
  fetchList.mockReset();
});
afterEach(() => jest.useRealTimers());

test('the rendered list contains only runs the gateway returned', async () => {
  fetchList.mockResolvedValueOnce(page([{ run_id: 'r1', status: 'RUNNING' }, { run_id: 'r2', status: 'COMPLETED' }]));
  const { result } = renderHook(() => useRunList({ tenant: 'acme', intervalMs: 1000 }));
  await flush();
  expect(result.current.runs.map((run) => run.run_id)).toEqual(['r1', 'r2']);
  expect(result.current.total).toBe(2);
});

test('an empty result is reported as loaded and empty rather than as loading forever', async () => {
  fetchList.mockResolvedValueOnce(page([]));
  const { result } = renderHook(() => useRunList({ tenant: 'acme', intervalMs: 1000 }));
  await flush();
  expect(result.current.loaded).toBe(true);
  expect(result.current.runs).toEqual([]);
  expect(result.current.phase).toBe('stopped');
});

test('polling continues while any visible run is active and stops once all are terminal', async () => {
  fetchList
    .mockResolvedValueOnce(page([{ run_id: 'r1', status: 'RUNNING' }]))
    .mockResolvedValueOnce(page([{ run_id: 'r1', status: 'COMPLETED' }]));
  const { result } = renderHook(() => useRunList({ tenant: 'acme', intervalMs: 1000 }));
  await flush();
  expect(result.current.phase).toBe('polling');

  await act(async () => {
    jest.advanceTimersByTime(1000);
    await Promise.resolve();
  });
  expect(result.current.phase).toBe('stopped');
});

test('an unconfirmed failure keeps polling instead of clearing the list', async () => {
  fetchList
    .mockResolvedValueOnce(page([{ run_id: 'r1', status: 'RUNNING' }]))
    .mockRejectedValueOnce(new AgentswarmRequestError('网关错误', 'unconfirmed', 502));
  const { result } = renderHook(() => useRunList({ tenant: 'acme', intervalMs: 1000 }));
  await flush();
  await act(async () => {
    jest.advanceTimersByTime(1000);
    await Promise.resolve();
  });
  expect(result.current.degraded).toBe(true);
  expect(result.current.runs).toHaveLength(1);
  expect(result.current.phase).toBe('polling');
});

test('a classified refusal stops and explains itself', async () => {
  fetchList.mockRejectedValueOnce(new AgentswarmRequestError('租户错误', 'refused', 400));
  const { result } = renderHook(() => useRunList({ tenant: 'acme', intervalMs: 1000 }));
  await flush();
  expect(result.current.phase).toBe('stopped');
  expect(result.current.error).toBe('租户错误');
});

test('no tenant means no request', async () => {
  renderHook(() => useRunList({ tenant: undefined }));
  await flush();
  expect(fetchList).not.toHaveBeenCalled();
});