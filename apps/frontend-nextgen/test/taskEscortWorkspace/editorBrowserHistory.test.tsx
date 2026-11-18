/** @jest-environment jsdom */
import { useTaskEscortWorkspaceEditorNavigation } from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceEditorNavigation';
import { act, renderHook, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: jest.requireActual('history').createBrowserHistory() }));

it('protects actual browser back/forward via history v5 POP, without monkeypatching native pushState', async () => {
  const nativePushState = window.history.pushState;
  history.replace('/evolution-factory/task-escort?demo=1&view=dashboard');
  history.push('/evolution-factory/task-escort?demo=1&view=editor');
  const restore = jest.fn();
  const { result, rerender, unmount } = renderHook(
    ({ dirty }) => useTaskEscortWorkspaceEditorNavigation(dirty, restore, 'one'),
    { initialProps: { dirty: true } },
  );
  act(() => history.back());
  await waitFor(() => expect(result.current.leave).not.toBeNull());
  await waitFor(() => expect(window.location.search).toContain('view=editor'));
  expect(history.location.search).toContain('view=editor');
  expect(window.history.pushState).toBe(nativePushState);
  act(() => result.current.cancelLeave());
  expect(restore).not.toHaveBeenCalled();
  act(() => history.back());
  await waitFor(() => expect(result.current.leave).not.toBeNull());
  await waitFor(() => expect(window.location.search).toContain('view=editor'));
  act(() => result.current.discardAndLeave());
  await waitFor(() => expect(history.location.search).toContain('view=dashboard'));
  expect(restore).toHaveBeenCalledTimes(1);
  rerender({ dirty: false });
  act(() => history.forward());
  await waitFor(() => expect(history.location.search).toContain('view=editor'));
  expect(result.current.leave).toBeNull();
  unmount();
});

it('permits browser POP between period-only editor entries and still blocks the following real exit', async () => {
  history.replace('/evolution-factory/task-escort?demo=1&view=editor&period=7d');
  history.push('/evolution-factory/task-escort?demo=1&view=editor&period=today');
  const restore = jest.fn();
  const { result } = renderHook(() => useTaskEscortWorkspaceEditorNavigation(true, restore, 'one'));
  act(() => history.back());
  await waitFor(() => expect(history.location.search).toContain('period=7d'));
  expect(result.current.leave).toBeNull();
  expect(restore).not.toHaveBeenCalled();
  act(() => history.forward());
  await waitFor(() => expect(history.location.search).toContain('period=today'));
  expect(result.current.leave).toBeNull();
  act(() => history.push('/evolution-factory/task-escort?demo=1&view=issues&period=today'));
  expect(history.location.search).toContain('view=editor');
  expect(result.current.leave).not.toBeNull();
  expect(restore).not.toHaveBeenCalled();
});

it('permits browser period-only POP on live editor routes without demo and still guards raw demo context changes', async () => {
  history.replace('/evolution-factory/task-escort?view=editor&period=7d&workflowId=one#node');
  history.push('/evolution-factory/task-escort?workflowId=one&period=today&view=editor#node');
  const restore = jest.fn();
  const { result } = renderHook(() => useTaskEscortWorkspaceEditorNavigation(true, restore, 'one'));
  act(() => history.back());
  await waitFor(() => expect(history.location.search).toContain('period=7d'));
  expect(window.location.search).toContain('period=7d');
  expect(history.location.hash).toBe('#node');
  expect(result.current.leave).toBeNull();
  expect(restore).not.toHaveBeenCalled();
  act(() => history.forward());
  await waitFor(() => expect(history.location.search).toContain('period=today'));
  expect(window.location.search).toContain('period=today');
  expect(result.current.leave).toBeNull();
  const original = history.location;
  act(() => history.push('/evolution-factory/task-escort?workflowId=one&period=today&view=editor&demo=1#node'));
  expect(history.location).toBe(original);
  expect(result.current.leave).not.toBeNull();
  expect(restore).not.toHaveBeenCalled();
});
