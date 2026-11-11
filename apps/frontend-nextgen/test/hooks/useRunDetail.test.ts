/** @jest-environment jsdom */
import { act, renderHook } from '@testing-library/react';
import { useRunDetail } from '@/hooks/useRunDetail';
import { AgentswarmRequestError } from '@/services/agentswarm/http';
import { getRunDetail } from '@/services/agentswarm/service';

jest.mock('@/services/agentswarm/service', () => ({
  getRun: jest.fn(),
  listRuns: jest.fn(),
  cancelRun: jest.fn(),
  getRunDetail: jest.fn(),
}));

const fetchDetail = getRunDetail as jest.Mock;
const flush = async () => {
  await act(async () => {
    await Promise.resolve();
  });
};

beforeEach(() => fetchDetail.mockReset());

test('no detail request is made while a run is active and unopened', async () => {
  renderHook(() => useRunDetail({ tenant: 'acme', runId: 'r1', opened: false, terminal: false }));
  await flush();
  expect(fetchDetail).not.toHaveBeenCalled();
});

test('opening the view reads the detail exactly once', async () => {
  fetchDetail.mockResolvedValue({ run_id: 'r1', status: 'RUNNING', agents: [] });
  const { result, rerender } = renderHook(
    ({ opened }: { opened: boolean }) => useRunDetail({ tenant: 'acme', runId: 'r1', opened, terminal: false }),
    { initialProps: { opened: false } },
  );
  await flush();
  expect(fetchDetail).not.toHaveBeenCalled();

  rerender({ opened: true });
  await flush();
  expect(fetchDetail).toHaveBeenCalledTimes(1);
  expect(result.current.phase).toBe('ready');
});

test('a terminal run reads the detail once without the user opening it', async () => {
  fetchDetail.mockResolvedValue({ run_id: 'r1', status: 'COMPLETED', agents: [] });
  renderHook(() => useRunDetail({ tenant: 'acme', runId: 'r1', opened: false, terminal: true }));
  await flush();
  expect(fetchDetail).toHaveBeenCalledTimes(1);
});

test('a later terminal flip does not refetch detail already loaded', async () => {
  fetchDetail.mockResolvedValue({ run_id: 'r1', status: 'RUNNING', agents: [] });
  const { rerender } = renderHook(
    ({ terminal }: { terminal: boolean }) => useRunDetail({ tenant: 'acme', runId: 'r1', opened: false, terminal }),
    { initialProps: { terminal: false } },
  );
  await flush();
  rerender({ terminal: true });
  await flush();
  expect(fetchDetail).toHaveBeenCalledTimes(1);
});

test('a different run reads its own detail', async () => {
  fetchDetail.mockResolvedValue({ run_id: 'r2', status: 'COMPLETED', agents: [] });
  const { rerender } = renderHook(
    ({ runId }: { runId: string }) => useRunDetail({ tenant: 'acme', runId, opened: false, terminal: true }),
    { initialProps: { runId: 'r1' } },
  );
  await flush();
  rerender({ runId: 'r2' });
  await flush();
  expect(fetchDetail.mock.calls.map(([, id]) => id)).toEqual(['r1', 'r2']);
});

test('a missing run explains itself instead of rendering empty detail', async () => {
  fetchDetail.mockRejectedValue(new AgentswarmRequestError('run not found', 'not_found', 404));
  const { result } = renderHook(() => useRunDetail({ tenant: 'acme', runId: 'r1', opened: true, terminal: false }));
  await flush();
  expect(result.current.phase).toBe('error');
  expect(result.current.error).toContain('不存在');
});