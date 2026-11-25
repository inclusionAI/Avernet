/** @jest-environment jsdom */
import { useTaskEscortWorkspaceEditorNavigation } from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceEditorNavigation';
import { act, renderHook } from '@testing-library/react';
import { history as mockHistory } from '@umijs/max';

jest.mock('@umijs/max', () => ({
  history: jest.requireActual('history').createMemoryHistory({
    initialEntries: ['/outside', '/evolution-factory/task-escort?demo=1&view=editor'],
    initialIndex: 1,
  }),
}));

beforeEach(() => {
  mockHistory.replace('/evolution-factory/task-escort?demo=1&view=editor');
});

it.each(['push', 'replace'] as const)(
  'blocks %s to local query or other application routes, supports cancel and confirmed retry',
  (method) => {
    const restore = jest.fn();
    const { result } = renderHook(() => useTaskEscortWorkspaceEditorNavigation(true, restore, 'one'));
    act(() => mockHistory[method]('/evolution-factory/task-escort?demo=1&view=issues'));
    expect(mockHistory.location.search).toContain('view=editor');
    expect(result.current.leave).not.toBeNull();
    act(() => result.current.cancelLeave());
    expect(result.current.leave).toBeNull();
    act(() => mockHistory[method]('/outside'));
    expect(mockHistory.location.pathname).not.toBe('/outside');
    act(() => result.current.discardAndLeave());
    expect(restore).toHaveBeenCalledTimes(1);
    expect(mockHistory.location.pathname).toBe('/outside');
  },
);

it('guards browser POP, beforeunload, and removes all blockers after unmount', () => {
  mockHistory.push('/evolution-factory/task-escort?demo=1&view=editor');
  const { result, unmount } = renderHook(() => useTaskEscortWorkspaceEditorNavigation(true, jest.fn(), 'one'));
  const key = mockHistory.location.key;
  act(() => mockHistory.back());
  expect(mockHistory.location.key).toBe(key);
  expect(result.current.leave).not.toBeNull();
  const event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
  unmount();
  const cleanEvent = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(cleanEvent);
  expect(cleanEvent.defaultPrevented).toBe(false);
  act(() => mockHistory.push('/outside'));
  expect(mockHistory.location.pathname).toBe('/outside');
});

it('uses the same confirmation for local workflow changes without losing drafts on cancel', () => {
  const restore = jest.fn(),
    proceed = jest.fn();
  const { result, rerender } = renderHook(
    ({ dirty }) => useTaskEscortWorkspaceEditorNavigation(dirty, restore, 'one'),
    { initialProps: { dirty: true } },
  );
  act(() => result.current.requestLeave(proceed));
  expect(proceed).not.toHaveBeenCalled();
  act(() => result.current.cancelLeave());
  expect(restore).not.toHaveBeenCalled();
  act(() => result.current.requestLeave(proceed));
  act(() => result.current.discardAndLeave());
  expect(proceed).toHaveBeenCalledTimes(1);
  expect(restore).toHaveBeenCalledTimes(1);
  rerender({ dirty: false });
  act(() => result.current.requestLeave(proceed));
  expect(proceed).toHaveBeenCalledTimes(2);
});

it('does not block clean editor navigation or unload', () => {
  renderHook(() => useTaskEscortWorkspaceEditorNavigation(false, jest.fn(), 'one'));
  act(() => mockHistory.push('/evolution-factory/task-escort?view=dashboard'));
  expect(mockHistory.location.search).toBe('?view=dashboard');
  const event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
});

it('allows period-only query transitions without restoring draft state and rearms the next real exit guard', () => {
  mockHistory.replace('/evolution-factory/task-escort?demo=1&view=editor&period=7d');
  const restore = jest.fn();
  const { result } = renderHook(() => useTaskEscortWorkspaceEditorNavigation(true, restore, 'one'));
  act(() => mockHistory.push('/evolution-factory/task-escort?demo=1&view=editor&period=today'));
  expect(mockHistory.location.search).toContain('period=today');
  expect(result.current.leave).toBeNull();
  expect(restore).not.toHaveBeenCalled();
  act(() => mockHistory.replace('/evolution-factory/task-escort?view=editor&period=30d&demo=1'));
  expect(mockHistory.location.search).toContain('period=30d');
  expect(result.current.leave).toBeNull();
  act(() => mockHistory.push('/evolution-factory/task-escort?demo=1&view=issues&period=30d'));
  expect(mockHistory.location.search).toContain('view=editor');
  expect(result.current.leave).not.toBeNull();
  expect(restore).not.toHaveBeenCalled();
});

it.each([
  '/evolution-factory/task-escort?demo=1&view=runs&period=today',
  '/evolution-factory/task-escort?view=editor&period=today',
  '/evolution-factory/task-escort?demo=1&view=editor&workflowId=two&period=today',
  '/outside?demo=1&view=editor&period=today',
])('still blocks a period change accompanied by an actual view/mode/workflow/path exit: %s', (target) => {
  mockHistory.replace('/evolution-factory/task-escort?demo=1&view=editor&period=7d');
  const restore = jest.fn();
  const { result } = renderHook(() => useTaskEscortWorkspaceEditorNavigation(true, restore, 'one'));
  const original = mockHistory.location;
  act(() => mockHistory.push(target));
  expect(mockHistory.location).toBe(original);
  expect(result.current.leave).not.toBeNull();
  expect(restore).not.toHaveBeenCalled();
});

it.each(['push', 'replace'] as const)(
  'allows %s period-only editor changes on live routes without a demo parameter and rearms the exit guard',
  (method) => {
    mockHistory.replace('/evolution-factory/task-escort?view=editor&period=7d&workflowId=one&keep=space#node');
    const restore = jest.fn();
    const { result } = renderHook(() => useTaskEscortWorkspaceEditorNavigation(true, restore, 'one'));
    act(() =>
      mockHistory[method]('/evolution-factory/task-escort?keep=space&workflowId=one&period=today&view=editor#node'),
    );
    expect(mockHistory.location.search).toBe('?keep=space&workflowId=one&period=today&view=editor');
    expect(mockHistory.location.hash).toBe('#node');
    expect(result.current.leave).toBeNull();
    expect(restore).not.toHaveBeenCalled();
    const original = mockHistory.location;
    act(() =>
      mockHistory.push('/evolution-factory/task-escort?view=issues&period=today&workflowId=one&keep=space#node'),
    );
    expect(mockHistory.location).toBe(original);
    expect(result.current.leave).not.toBeNull();
    expect(restore).not.toHaveBeenCalled();
  },
);

it.each([
  '/evolution-factory/task-escort?view=editor&period=today&workflowId=one&keep=space#other-node',
  '/evolution-factory/task-escort?view=editor&period=today&workflowId=one&keep=other-space#node',
  '/evolution-factory/task-escort?view=editor&period=today&workflowId=one&keep=space&demo=1#node',
  '/evolution-factory/task-escort?view=editor&period=today&workflowId=one&keep=space&demo=0#node',
])('blocks period changes on live editor routes when raw hash or non-period query context differs: %s', (target) => {
  mockHistory.replace('/evolution-factory/task-escort?view=editor&period=7d&workflowId=one&keep=space#node');
  const restore = jest.fn();
  const { result } = renderHook(() => useTaskEscortWorkspaceEditorNavigation(true, restore, 'one'));
  const original = mockHistory.location;
  act(() => mockHistory.push(target));
  expect(mockHistory.location).toBe(original);
  expect(result.current.leave).not.toBeNull();
  expect(restore).not.toHaveBeenCalled();
});
