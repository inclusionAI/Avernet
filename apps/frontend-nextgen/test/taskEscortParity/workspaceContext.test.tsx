/** @jest-environment jsdom */
import { useTaskEscortRead } from '@/hooks/taskEscortParity/useTaskEscortRead';
import { useTaskEscortWorkspaceContext } from '@/hooks/taskEscortParity/useTaskEscortWorkspaceContext';
import { readTaskEscortWorkflows } from '@/services/taskEscortParity/workspaceService';
import { act, renderHook, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';

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
jest.mock('@/services/taskEscortParity/workspaceService', () => ({ readTaskEscortWorkflows: jest.fn() }));
const read = jest.mocked(readTaskEscortWorkflows);
const rows = [
  { id: 'a', title: 'A', runCount: 3, lastStatus: 'succeeded', ownerId: 'owner-a' },
  { id: 'b', title: 'B', runCount: null, lastStatus: null, ownerId: null },
];
const scope = { key: 'user:space', ready: true, userId: 'user', loading: false, retry: jest.fn() };
beforeEach(() => {
  jest.clearAllMocks();
  history.replace('/evolution-factory/task-escort?view=runs&host=yes#keep');
  read.mockResolvedValue(rows);
});

it('selects the query workflow or first real row and passes only the shared typed context', async () => {
  history.replace('/evolution-factory/task-escort?tab=evolution&evoTab=remedies&workflowId=b&runId=real');
  const { result } = renderHook(() => useTaskEscortWorkspaceContext(scope));
  await waitFor(() => expect(result.current.context.ready).toBe(true));
  expect(result.current.page).toBe('experiences');
  expect(result.current.context).toMatchObject({
    workflowId: 'b',
    workflow: rows[1],
    scopeKey: scope.key,
    userId: 'user',
    query: { runId: 'real' },
  });
  expect(read).toHaveBeenCalledWith('user', expect.any(AbortSignal));
  act(() => history.push('/evolution-factory/task-escort?workflowId=not-accessible'));
  expect(result.current.context).toMatchObject({ workflow: null, workflowId: '', ready: false });
  expect(result.current.context.error).toMatch(/所选工作流不存在或当前身份无权访问/);
});

it('uses latest committed history for sequential dashboard navigation + selection and resets stale detail focus', async () => {
  const { result } = renderHook(() => useTaskEscortWorkspaceContext(scope));
  await waitFor(() => expect(result.current.context.ready).toBe(true));
  const { navigate } = result.current.context;
  const { selectWorkflow } = result.current;
  act(() => {
    navigate('workbench', { runId: 'r', analysisId: 'a', issueSignature: 'i' });
    selectWorkflow('b');
  });
  expect(Object.fromEntries(new URLSearchParams(history.location.search))).toMatchObject({
    view: 'issues',
    workflowId: 'b',
    host: 'yes',
    tab: 'evolution',
    evoTab: 'diagnosis',
  });
  expect(history.location.search).not.toMatch(/runId|analysisId|issueSignature/);
  expect(history.location.hash).toBe('#keep');
  expect(read).toHaveBeenCalledTimes(1);
});

it('honors registered editor history blockers for both navigation and workflow selection', async () => {
  history.replace('/evolution-factory/task-escort?view=editor&workflowId=a');
  const { result } = renderHook(() => useTaskEscortWorkspaceContext(scope));
  await waitFor(() => expect(result.current.context.ready).toBe(true));
  const unblock = history.block(() => undefined);
  act(() => {
    result.current.context.navigate('runs');
    result.current.selectWorkflow('b');
  });
  expect(result.current.page).toBe('editor');
  expect(result.current.context.workflowId).toBe('a');
  unblock();
});

it('blocks leaves for loading/error/empty and retries identity rather than reading before readiness', async () => {
  read.mockResolvedValue([]);
  const { result, rerender } = renderHook(({ current }) => useTaskEscortWorkspaceContext(current), {
    initialProps: { current: { ...scope, ready: false, error: 'identity unavailable' } },
  });
  expect(result.current.context).toMatchObject({
    ready: false,
    workflow: null,
    workflowId: '',
    error: 'identity unavailable',
  });
  act(() => result.current.context.refreshWorkflows());
  expect(scope.retry).toHaveBeenCalled();
  expect(read).not.toHaveBeenCalled();
  rerender({ current: { ...scope, error: '' } });
  await waitFor(() => expect(result.current.context.loading).toBe(false));
  expect(result.current.context).toMatchObject({ ready: false, workflow: null, workflowId: '' });
  read.mockRejectedValueOnce(new Error('403 forbidden'));
  act(() => result.current.context.refreshWorkflows());
  await waitFor(() => expect(result.current.context.error).toBe('403 forbidden'));
});

it('defaults to the first real workflow only when workflowId is absent', async () => {
  const { result } = renderHook(() => useTaskEscortWorkspaceContext(scope));
  await waitFor(() => expect(result.current.context.ready).toBe(true));
  expect(result.current.context).toMatchObject({ workflow: rows[0], workflowId: 'a', error: undefined });
});

it.each(['not-accessible', ''])(
  'fails closed for explicit workflowId=%j with old detail focus, without leaf reads',
  async (workflowId) => {
    history.replace(
      `/evolution-factory/task-escort?view=issues&workflowId=${workflowId}&runId=old-run&analysisId=old-analysis&issueSignature=old-issue&host=yes#keep`,
    );
    const leafRead = jest.fn(async () => 'must not load a fallback workflow');
    const { result } = renderHook(() => {
      const workspace = useTaskEscortWorkspaceContext(scope);
      const leaf = useTaskEscortRead(workspace.context, 'focused-leaf', leafRead);
      return { ...workspace, leaf };
    });
    await waitFor(() => expect(result.current.context.loading).toBe(false));
    expect(result.current.context).toMatchObject({ ready: false, workflow: null, workflowId: '' });
    expect(result.current.context.error).toMatch(/所选工作流不存在或当前身份无权访问/);
    expect(result.current.workflows).toEqual(rows);
    expect(result.current.leaf.data).toBeNull();
    expect(leafRead).not.toHaveBeenCalled();
    act(() => result.current.context.refreshWorkflows());
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.context.loading).toBe(false));
    expect(result.current.context.ready).toBe(false);
    expect(result.current.context.workflow).toBeNull();
    expect(leafRead).not.toHaveBeenCalled();
    act(() => result.current.selectWorkflow('b'));
    await waitFor(() => expect(result.current.context.ready).toBe(true));
    expect(result.current.context).toMatchObject({ workflow: rows[1], workflowId: 'b', error: undefined });
    expect(history.location.search).not.toMatch(/runId|analysisId|issueSignature/);
    expect(history.location.search).toContain('host=yes');
    expect(history.location.hash).toBe('#keep');
  },
);

it('keeps identity-bound creation ready for an empty list without enabling workflow leaves', async () => {
  read.mockResolvedValue([]);
  const { result } = renderHook(() => useTaskEscortWorkspaceContext(scope));
  await waitFor(() => expect(result.current.context.loading).toBe(false));
  expect(result.current.context).toMatchObject({ creationReady: true, ready: false, workflow: null });
  await act(async () => {
    result.current.context.refreshWorkflows();
    result.current.context.navigate('editor', { workflowId: 'new-flow' });
  });
  expect(new URLSearchParams(history.location.search).get('workflowId')).toBe('new-flow');
  expect(new URLSearchParams(history.location.search).get('view')).toBe('editor');
});

it('navigates to a newly created workflow before list refresh and clears unrelated focus', async () => {
  history.replace(
    '/evolution-factory/task-escort?view=runs&workflowId=a&runId=old&analysisId=old&nodeId=old&baseline=old&host=yes#keep',
  );
  const { result } = renderHook(() => useTaskEscortWorkspaceContext(scope));
  await waitFor(() => expect(result.current.context.ready).toBe(true));
  act(() => result.current.context.navigate('editor', { workflowId: 'new-flow' }));
  expect(new URLSearchParams(history.location.search).get('workflowId')).toBe('new-flow');
  expect(history.location.search).not.toMatch(/runId|analysisId|nodeId|baseline/);
  expect(history.location.search).toContain('host=yes');
  expect(history.location.hash).toBe('#keep');
});

it('defers post-create refresh until editor navigation is committed, preserving a blocked dirty editor', async () => {
  history.replace('/evolution-factory/task-escort?view=editor&workflowId=a');
  const { result } = renderHook(() => useTaskEscortWorkspaceContext(scope));
  await waitFor(() => expect(result.current.context.ready).toBe(true));
  let proceed!: () => void;
  const unblock = history.block((transition) => {
    proceed = transition.retry;
  });
  act(() => result.current.context.openCreatedWorkflow?.('new-flow'));
  expect(result.current.context.ready).toBe(true);
  expect(result.current.context.workflowId).toBe('a');
  expect(read).toHaveBeenCalledTimes(1);
  read.mockResolvedValue([...rows, { ...rows[0], id: 'new-flow', title: 'New' }]);
  await act(async () => {
    unblock();
    proceed();
  });
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(result.current.context.workflowId).toBe('new-flow'));
  expect(result.current.context.ready).toBe(true);
});

it('exposes introduction only after an identity-ready list resolves empty, not during refresh', async () => {
  let resolve!: (value: typeof rows) => void;
  read.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const { result } = renderHook(() => useTaskEscortWorkspaceContext(scope));
  expect(result.current.showIntroduction).toBe(false);
  expect(result.current.context.loading).toBe(true);
  await act(async () => resolve([]));
  expect(result.current.showIntroduction).toBe(true);
  expect(result.current.context.ready).toBe(false);
  read.mockResolvedValue(rows);
  act(() => result.current.context.refreshWorkflows());
  expect(result.current.showIntroduction).toBe(false);
  await waitFor(() => expect(result.current.context.ready).toBe(true));
  expect(result.current.showIntroduction).toBe(false);
});

it.each(['missing', ''])('does not introduce an empty list for invalid explicit workflowId=%j', async (id) => {
  history.replace(`/evolution-factory/task-escort?view=dashboard&workflowId=${id}`);
  read.mockResolvedValue([]);
  const { result } = renderHook(() => useTaskEscortWorkspaceContext(scope));
  await waitFor(() => expect(result.current.context.error).toMatch(/所选工作流/));
  expect(result.current.showIntroduction).toBe(false);
});

it('withdraws empty introduction synchronously on scope changes and rejects late empty responses', async () => {
  read.mockResolvedValueOnce([]);
  const { result, rerender } = renderHook(({ current }) => useTaskEscortWorkspaceContext(current), {
    initialProps: { current: scope },
  });
  await waitFor(() => expect(result.current.showIntroduction).toBe(true));
  let resolveOld!: (value: typeof rows) => void;
  read.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolveOld = done;
      }),
  );
  rerender({ current: { ...scope, key: 'other:space', userId: 'other' } });
  expect(result.current.showIntroduction).toBe(false);
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  const signal = read.mock.calls[1][1]!;
  rerender({ current: { ...scope, ready: false } });
  expect(result.current.showIntroduction).toBe(false);
  expect(signal.aborted).toBe(true);
  await act(async () => resolveOld([]));
  expect(result.current.showIntroduction).toBe(false);
});

it('never introduces a failed list or an unready identity', async () => {
  read.mockRejectedValue(new Error('403 forbidden'));
  const { result, rerender } = renderHook(({ current }) => useTaskEscortWorkspaceContext(current), {
    initialProps: { current: { ...scope, ready: false } },
  });
  expect(result.current.showIntroduction).toBe(false);
  expect(read).not.toHaveBeenCalled();
  rerender({ current: scope });
  await waitFor(() => expect(result.current.context.error).toBe('403 forbidden'));
  expect(result.current.showIntroduction).toBe(false);
});
