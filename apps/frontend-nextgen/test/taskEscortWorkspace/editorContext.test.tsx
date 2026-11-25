/** @jest-environment jsdom */
import {
  TaskEscortEditorProvider,
  useTaskEscortWorkspaceEditorSession,
} from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceEditorContext';
import { act, renderHook } from '@testing-library/react';
import React from 'react';

it('retains saved drafts across editor hook unmount and clears on identity, mode or reset changes', () => {
  let key = 'first',
    mode: 'demo' | 'live' = 'demo',
    reset = 0;
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <TaskEscortEditorProvider workspaceKey={key} mode={mode} resetKey={reset}>
      {children}
    </TaskEscortEditorProvider>
  );
  const { result, rerender } = renderHook(() => useTaskEscortWorkspaceEditorSession(), { wrapper });
  const initial = result.current;
  act(() => {
    initial.patch('one', { name: 'saved' });
    initial.save('one');
  });
  rerender();
  expect(result.current).toBe(initial);
  reset += 1;
  rerender();
  expect(result.current).not.toBe(initial);
  expect(result.current.getSnapshot('one').nodes[1].name).toBe('数据采集');
  const resetService = result.current;
  key = 'another';
  rerender();
  expect(result.current).not.toBe(resetService);
  mode = 'live';
  rerender();
  expect(result.current.getSnapshot('one').nodes).toEqual([]);
});

it('requires a workspace provider instead of falling back to a shared global session', () => {
  const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
  expect(() => renderHook(() => useTaskEscortWorkspaceEditorSession())).toThrow('TaskEscortEditorProvider');
  spy.mockRestore();
});
