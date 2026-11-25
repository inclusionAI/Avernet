/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortRead } from '@/hooks/taskEscortParity/useTaskEscortRead';
import { act, renderHook, waitFor } from '@testing-library/react';

const context: TaskEscortParityContext = {
  scopeKey: 'identity:space',
  ready: true,
  userId: 'user-a',
  workflowId: 'wf-a',
  workflow: { id: 'wf-a', title: 'Workflow A', runCount: null, lastStatus: null, ownerId: null },
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

it('gates on readiness and clears already rendered data synchronously on each isolation dimension', async () => {
  const read = jest.fn(async () => 'real-value');
  const { result, rerender } = renderHook(({ ctx, key }) => useTaskEscortRead(ctx, key, read), {
    initialProps: { ctx: context, key: 'runs' },
  });
  await waitFor(() => expect(result.current.data).toBe('real-value'));
  for (const patch of [{ scopeKey: 'other' }, { workflowId: 'wf-b' }, { userId: 'other' }]) {
    rerender({ ctx: { ...context, ...patch, ready: false }, key: 'runs' });
    expect(result.current).toMatchObject({ data: null, error: null, loading: false });
    rerender({ ctx: { ...context, ...patch }, key: 'runs' });
    expect(result.current.data).toBeNull();
    await waitFor(() => expect(result.current.data).toBe('real-value'));
  }
  rerender({ ctx: context, key: 'nodes' });
  expect(result.current.data).toBeNull();
  await waitFor(() => expect(result.current.data).toBe('real-value'));
  const calls = read.mock.calls.length;
  rerender({ ctx: { ...context, ready: false }, key: 'nodes' });
  expect(result.current).toMatchObject({ data: null, error: null, loading: false });
  act(() => result.current.refresh());
  expect(read).toHaveBeenCalledTimes(calls);
});

it('aborts scope changes and ignores both late success and late errors, including after unmount', async () => {
  const first = deferred<string>();
  const second = deferred<string>();
  const signals: AbortSignal[] = [];
  const read = jest.fn((signal: AbortSignal) => {
    signals.push(signal);
    return signals.length === 1 ? first.promise : second.promise;
  });
  const { result, rerender, unmount } = renderHook(({ ctx }) => useTaskEscortRead(ctx, 'resource', read), {
    initialProps: { ctx: context },
  });
  rerender({ ctx: { ...context, workflowId: 'wf-b' } });
  expect(signals[0].aborted).toBe(true);
  await act(async () => first.resolve('stale'));
  expect(result.current.data).toBeNull();
  await act(async () => second.resolve('current'));
  expect(result.current.data).toBe('current');
  act(() => result.current.refresh());
  expect(result.current.data).toBeNull();
  unmount();
  expect(signals.every((signal) => signal.aborted)).toBe(true);
});

it('returns real failures without fallback and supports retry, including synchronous throws', async () => {
  const read = jest
    .fn<Promise<string>, [AbortSignal]>()
    .mockRejectedValueOnce(new Error('403 forbidden'))
    .mockImplementationOnce(() => {
      throw new Error('sync failure');
    })
    .mockResolvedValue('authorized');
  const { result } = renderHook(() => useTaskEscortRead(context, 'resource', read));
  await waitFor(() => expect(result.current.error).toBe('403 forbidden'));
  expect(result.current.data).toBeNull();
  act(() => result.current.refresh());
  expect(result.current.error).toBeNull();
  await waitFor(() => expect(result.current.error).toBe('sync failure'));
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.data).toBe('authorized'));
});

it('ignores a late rejected read and does not invoke reads while not ready', async () => {
  const old = deferred<string>();
  const read = jest.fn(() => old.promise);
  const { result, rerender } = renderHook(({ ctx }) => useTaskEscortRead(ctx, 'read', read), {
    initialProps: { ctx: { ...context, ready: false } },
  });
  expect(read).not.toHaveBeenCalled();
  rerender({ ctx: context });
  rerender({ ctx: { ...context, ready: false } });
  await act(async () => old.reject(new Error('stale error')));
  expect(result.current).toMatchObject({ data: null, error: null, loading: false });
});
