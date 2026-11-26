/** @jest-environment jsdom */
import { useWorkflowList, type UseWorkflowListResult } from '@/hooks/useWorkflowList';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import { fetchWorkflows, type WorkflowListItem } from '@/services/tasks/taskService';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/services/tasks/taskService', () => ({ fetchWorkflows: jest.fn() }));

const mockedFetch = jest.mocked(fetchWorkflows);
const contextA: TaskComposerContext = {
  sourceType: 'bot', ownerUserId: 'viewer-1', ownerBotId: 'bot-a:owner-a', mainSessionId: 'session-a',
};
const contextB: TaskComposerContext = { ...contextA, ownerBotId: 'bot-b:owner-b' };
const rowsA: WorkflowListItem[] = [{ workflowId: 'workflow-a', title: 'A', packId: null, updatedAt: 1 }];
const rowsB: WorkflowListItem[] = [{ workflowId: 'workflow-b', title: 'B', packId: null, updatedAt: 2 }];

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setup(context: TaskComposerContext | null = contextA) {
  const renders: UseWorkflowListResult[] = [];
  const hook = renderHook(({ context }: { context: TaskComposerContext | null }) => {
    const state = useWorkflowList(context);
    renders.push(state);
    return state;
  }, { initialProps: { context } });
  return { ...hook, renders };
}

beforeEach(() => { mockedFetch.mockReset(); });

it('is lazy, deduplicates pending loads, and keeps one successful list across same-owner sessions', async () => {
  const pending = deferred<WorkflowListItem[]>();
  mockedFetch.mockReturnValue(pending.promise);
  const { result, rerender } = setup();
  expect(mockedFetch).not.toHaveBeenCalled();
  expect(result.current).toMatchObject({ workflows: [], workflowsLoading: false, workflowsError: null });
  let first!: Promise<void>;
  let second!: Promise<void>;
  let settled = false;
  await act(async () => {
    first = result.current.loadWorkflows();
    second = result.current.loadWorkflows();
    void second.then(() => { settled = true; });
    await Promise.resolve();
  });
  expect(mockedFetch).toHaveBeenCalledTimes(1);
  expect(mockedFetch).toHaveBeenCalledWith('viewer-1', 'bot-a:owner-a');
  expect(result.current.workflowsLoading).toBe(true);
  expect(settled).toBe(false);
  await act(async () => { pending.resolve(rowsA); await Promise.all([first, second]); });
  expect(result.current).toMatchObject({ workflows: rowsA, workflowsLoading: false, workflowsError: null });
  rerender({ context: { ...contextA, mainSessionId: 'session-other', mainSessionName: 'Other session' } });
  await act(async () => { await result.current.loadWorkflows(); });
  expect(mockedFetch).toHaveBeenCalledTimes(1);
  expect(result.current.workflows).toEqual(rowsA);
});

it.each([
  null,
  { ...contextA, ownerUserId: '' },
  { ...contextA, ownerUserId: '   ' },
  { ...contextA, ownerBotId: '' },
  { ...contextA, ownerBotId: '   ' },
] as Array<TaskComposerContext | null>)('does not request missing context/owner/Bot: %j', async (context) => {
  const { result } = setup(context);
  await act(async () => { await result.current.loadWorkflows(); });
  expect(mockedFetch).not.toHaveBeenCalled();
  expect(result.current).toMatchObject({ workflows: [], workflowsLoading: false, workflowsError: null });
});

it('clears errors on retry and caches a successful empty list without fixture fallback', async () => {
  const retry = deferred<WorkflowListItem[]>();
  mockedFetch.mockRejectedValueOnce(new Error('Workflow service unavailable')).mockReturnValueOnce(retry.promise);
  const { result } = setup();
  await act(async () => { await expect(result.current.loadWorkflows()).resolves.toBeUndefined(); });
  expect(result.current).toMatchObject({ workflows: [], workflowsLoading: false, workflowsError: 'Workflow service unavailable' });
  let retryLoad!: Promise<void>;
  await act(async () => { retryLoad = result.current.loadWorkflows(); await Promise.resolve(); });
  expect(result.current).toMatchObject({ workflows: [], workflowsLoading: true, workflowsError: null });
  await act(async () => { retry.resolve([]); await retryLoad; });
  await act(async () => { await result.current.loadWorkflows(); });
  expect(result.current).toMatchObject({ workflows: [], workflowsLoading: false, workflowsError: null });
  expect(mockedFetch).toHaveBeenCalledTimes(2);
});

it.each(['success', 'error'] as const)('masks old list/error on every scope-switch render: %s', async (outcome) => {
  if (outcome === 'success') mockedFetch.mockResolvedValueOnce(rowsA);
  else mockedFetch.mockRejectedValueOnce(new Error('Old scope error'));
  const { result, rerender, renders } = setup();
  await act(async () => { await result.current.loadWorkflows(); });
  renders.length = 0;
  rerender({ context: contextB });
  expect(renders.length).toBeGreaterThan(0);
  for (const state of renders) {
    expect(state).toMatchObject({ workflows: [], workflowsLoading: false, workflowsError: null });
  }
  expect(mockedFetch).toHaveBeenCalledTimes(1);
});

it.each(['success', 'error'] as const)('ignores A %s/finalization while B is pending, then accepts only B', async (outcome) => {
  const a = deferred<WorkflowListItem[]>();
  const b = deferred<WorkflowListItem[]>();
  mockedFetch.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
  const { result, rerender } = setup();
  let loadA!: Promise<void>;
  let loadB!: Promise<void>;
  await act(async () => { loadA = result.current.loadWorkflows(); await Promise.resolve(); });
  rerender({ context: contextB });
  expect(result.current).toMatchObject({ workflows: [], workflowsLoading: false, workflowsError: null });
  await act(async () => { loadB = result.current.loadWorkflows(); await Promise.resolve(); });
  await act(async () => {
    if (outcome === 'success') a.resolve(rowsA);
    else a.reject(new Error('Stale A error'));
    await loadA;
  });
  expect(result.current).toMatchObject({ workflows: [], workflowsLoading: true, workflowsError: null });
  await act(async () => { b.resolve(rowsB); await loadB; });
  expect(result.current).toMatchObject({ workflows: rowsB, workflowsLoading: false, workflowsError: null });
});

it.each(['success', 'error'] as const)('invalidates A when switching to null before its %s', async (outcome) => {
  const pending = deferred<WorkflowListItem[]>();
  mockedFetch.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(rowsB);
  const { result, rerender } = setup();
  let loadA!: Promise<void>;
  await act(async () => { loadA = result.current.loadWorkflows(); await Promise.resolve(); });
  rerender({ context: null });
  expect(result.current).toMatchObject({ workflows: [], workflowsLoading: false, workflowsError: null });
  await act(async () => { await result.current.loadWorkflows(); });
  await act(async () => {
    if (outcome === 'success') pending.resolve(rowsA);
    else pending.reject(new Error('Stale A error'));
    await loadA;
  });
  expect(result.current).toMatchObject({ workflows: [], workflowsLoading: false, workflowsError: null });
  expect(mockedFetch).toHaveBeenCalledTimes(1);
  rerender({ context: contextA });
  await act(async () => { await result.current.loadWorkflows(); });
  expect(mockedFetch).toHaveBeenCalledTimes(2);
  expect(result.current.workflows).toEqual(rowsB);
});

it.each(['success', 'error'] as const)('rejects ABA generation reuse when old A ends in %s', async (outcome) => {
  const oldA = deferred<WorkflowListItem[]>();
  const b = deferred<WorkflowListItem[]>();
  const newA = deferred<WorkflowListItem[]>();
  mockedFetch.mockReturnValueOnce(oldA.promise).mockReturnValueOnce(b.promise).mockReturnValueOnce(newA.promise);
  const { result, rerender } = setup();
  let first!: Promise<void>;
  let middle!: Promise<void>;
  let last!: Promise<void>;
  await act(async () => { first = result.current.loadWorkflows(); await Promise.resolve(); });
  rerender({ context: contextB });
  await act(async () => { middle = result.current.loadWorkflows(); await Promise.resolve(); });
  rerender({ context: contextA });
  await act(async () => { last = result.current.loadWorkflows(); await Promise.resolve(); });
  expect(mockedFetch).toHaveBeenCalledTimes(3);
  await act(async () => {
    if (outcome === 'success') oldA.resolve(rowsA);
    else oldA.reject(new Error('Stale first A'));
    b.reject(new Error('Stale B'));
    await Promise.all([first, middle]);
  });
  expect(result.current).toMatchObject({ workflows: [], workflowsLoading: true, workflowsError: null });
  await act(async () => { newA.resolve(rowsB); await last; });
  expect(result.current).toMatchObject({ workflows: rowsB, workflowsLoading: false, workflowsError: null });
  await act(async () => { await result.current.loadWorkflows(); });
  expect(mockedFetch).toHaveBeenCalledTimes(3);
});

it('a late failure cannot evict a newer successful cache', async () => {
  const oldA = deferred<WorkflowListItem[]>();
  mockedFetch.mockReturnValueOnce(oldA.promise).mockResolvedValueOnce(rowsB);
  const { result, rerender } = setup();
  let oldLoad!: Promise<void>;
  await act(async () => { oldLoad = result.current.loadWorkflows(); await Promise.resolve(); });
  rerender({ context: contextB });
  await act(async () => { await result.current.loadWorkflows(); });
  await act(async () => { oldA.reject(new Error('Old scope failure')); await oldLoad; });
  await act(async () => { await result.current.loadWorkflows(); });
  expect(result.current).toMatchObject({ workflows: rowsB, workflowsLoading: false, workflowsError: null });
  expect(mockedFetch).toHaveBeenCalledTimes(2);
});

it('invalidates an owner-only scope switch even if the Bot ID stays the same', async () => {
  const old = deferred<WorkflowListItem[]>();
  mockedFetch.mockReturnValueOnce(old.promise).mockResolvedValueOnce(rowsB);
  const { result, rerender } = setup();
  let oldLoad!: Promise<void>;
  await act(async () => { oldLoad = result.current.loadWorkflows(); await Promise.resolve(); });
  rerender({ context: { ...contextA, ownerUserId: 'viewer-2' } });
  await act(async () => { await result.current.loadWorkflows(); });
  expect(mockedFetch).toHaveBeenLastCalledWith('viewer-2', 'bot-a:owner-a');
  await act(async () => { old.resolve(rowsA); await oldLoad; });
  expect(result.current).toMatchObject({ workflows: rowsB, workflowsLoading: false, workflowsError: null });
});

it('does not keep a historical successful A cache after switching through B without loading B', async () => {
  mockedFetch.mockResolvedValueOnce(rowsA).mockResolvedValueOnce(rowsB);
  const { result, rerender } = setup();
  await act(async () => { await result.current.loadWorkflows(); });
  rerender({ context: contextB });
  rerender({ context: contextA });
  expect(result.current.workflows).toEqual([]);
  await act(async () => { await result.current.loadWorkflows(); });
  expect(mockedFetch).toHaveBeenCalledTimes(2);
  expect(result.current.workflows).toEqual(rowsB);
});

it('also exposes synchronous/non-Error failures and allows an explicit retry', async () => {
  mockedFetch.mockImplementationOnce(() => { throw 'Unavailable'; }).mockResolvedValueOnce(rowsA);
  const { result } = setup();
  await act(async () => { await result.current.loadWorkflows(); });
  expect(result.current.workflows).toEqual([]);
  expect(result.current.workflowsLoading).toBe(false);
  expect(result.current.workflowsError).toBeTruthy();
  await act(async () => { await result.current.loadWorkflows(); });
  expect(result.current).toMatchObject({ workflows: rowsA, workflowsLoading: false, workflowsError: null });
  expect(mockedFetch).toHaveBeenCalledTimes(2);
});
