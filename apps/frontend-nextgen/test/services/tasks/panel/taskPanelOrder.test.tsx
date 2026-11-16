/** @jest-environment jsdom */
import { createRef } from 'react';
import { act, render } from '@testing-library/react';
import type { PanelHandle } from '@tc-chat/core';
import { ChatLayout } from '@tc-chat/ui/es/ChatLayout';
import { onTaskPanelTabClosed, restoreOrderedTaskPanelTabs, taskNavigationAwareHandle } from '@/services/tasks/panel/taskPanelHandleAdapter';

const mockClose = jest.fn();
jest.mock('@/services/tasks/panel/taskPanelRuntime', () => ({
  peekTaskPanelController: () => ({ getSnapshot: () => ({ activeTaskId: 'new' }), closeTask: mockClose }),
}));
jest.mock('@tc-chat/ui/es/SidePanelContent', () => ({ SidePanelContent: () => <div data-task-panel-root="true" /> }));
const tab = (id: string) => ({ id: `task-${id}`, type: 'taskPanel.TaskLoopView', title: id, closable: true, params: { taskId: id } });

it('restores chronological SDK order even when the newest card mounted first, independently of focus', () => {
  const ref = createRef<PanelHandle>();
  render(<ChatLayout.Panel ref={ref} onTabClose={onTaskPanelTabClosed} />);
  act(() => { ref.current!.openTab(tab('new')); });
  const handle = taskNavigationAwareHandle(ref.current!);
  act(() => { restoreOrderedTaskPanelTabs(handle, [tab('old'), tab('new')], 'task-new', true); });
  const state = ref.current!.flushContext().panelState;
  expect(state.tabs.map(value => value.id)).toEqual(['task-old', 'task-new']);
  expect(state.activeTabId).toBe('task-new');
  expect(mockClose).not.toHaveBeenCalled();

  act(() => { restoreOrderedTaskPanelTabs(handle, [tab('old'), tab('new')], 'task-old', true); });
  const selected = ref.current!.flushContext().panelState;
  expect(selected.tabs.map(value => value.id)).toEqual(['task-old', 'task-new']);
  expect(selected.activeTabId).toBe('task-old');
  expect(mockClose).not.toHaveBeenCalled();
});
