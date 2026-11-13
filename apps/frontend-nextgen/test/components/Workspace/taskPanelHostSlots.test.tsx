/** @jest-environment jsdom */
import { useStaleTaskPanelRelease } from '@/components/Workspace/persistentTaskPanelStaleRelease';
import { getExtensionSlots, registerExtensionSlots } from '@/extensions/slots';
import { UmdPanel } from '@/services/bcs/UmdPanel';
import { resolveUmdComponent } from '@/services/bcs/resolveUmdModule';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { PanelHandle } from '@tc-chat/core';
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import { act, render, renderHook, screen } from '@testing-library/react';

jest.mock('@tc-chat/ui/es/SidePanelContent', () => ({ registerPanelContent: jest.fn() }));
jest.mock('@/services/bcs/resolveUmdModule');

beforeEach(() => {
  jest.clearAllMocks();
  registerExtensionSlots({ ...getExtensionSlots(), taskPanelHost: null });
  useTaskConversationNavigationStore.getState().resetAll();
  useTaskSidePanelStore.getState().resetAll();
});

it.each(['taskPanel.TaskLoopView', 'task-loop', 'TaskLoopView'])(
  'collab refuses historical task declaration %s without loading its supplied CDN', (entry) => {
    const props = { tab: { id: 'old-task', params: {
      cdn: 'https://untrusted.invalid/task.js', entry, _libraryName: 'taskPanel', data: { taskId: 't1' },
    } } } as unknown as PanelContentProps;
    render(<UmdPanel {...props} />);
    expect(screen.getByText('当前站点不支持任务副屏')).toBeTruthy();
    expect(resolveUmdComponent).not.toHaveBeenCalled();
  },
);

it('Open delegates task rendering and stale-route cleanup to its registered host', () => {
  const Adapter = jest.fn((props: PanelContentProps) => <span data-task-id={String(props.params?.taskId)}>trusted task panel</span>);
  const leaveConversation = jest.fn();
  registerExtensionSlots({ ...getExtensionSlots(), taskPanelHost: {
    Adapter, leaveConversation, wrapHandle: handle => handle, onTabClosed: jest.fn(),
  } });
  render(<UmdPanel {...({ tab: { id: 'task-t1', params: {
    entry: 'taskPanel.TaskLoopView', data: { taskId: 't1' },
  } } } as unknown as PanelContentProps)} />);
  expect(screen.getByText('trusted task panel')).toBeTruthy();
  expect(Adapter.mock.calls[0][0]).toMatchObject({ params: { taskId: 't1' } });
  const closePanelForce = jest.fn();
  renderHook(() => useStaleTaskPanelRelease({ closePanelForce } as unknown as PanelHandle));
  act(() => useTaskConversationNavigationStore.getState().requestStaleRouteRelease());
  expect(leaveConversation).toHaveBeenCalledTimes(1);
  expect(closePanelForce).toHaveBeenCalledTimes(1);
});

it('collab still closes stale generic panels without a task host', () => {
  const closePanelForce = jest.fn();
  renderHook(() => useStaleTaskPanelRelease({ closePanelForce } as unknown as PanelHandle));
  act(() => useTaskConversationNavigationStore.getState().requestStaleRouteRelease());
  expect(closePanelForce).toHaveBeenCalledTimes(1);
  expect(useTaskConversationNavigationStore.getState().staleRouteReleaseSignal).toBe(0);
});
