/** @vitest-environment jsdom */
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { createTaskPanelController } from '../../../src/controller/createTaskPanelController';
import { makeHost } from '../taskSwitcherFixtures';
import { NodeListViewV2 } from '../../../src/panel/v2/NodeListViewV2';
import type { TaskPanelNav } from '../../../src/panel/v2/taskPanelNav';
import type { TaskView } from '../../../src/panel/types';

function nav() {
  return {
    selectedNodeId: null,
    drillStack: [],
    selectRootNode: vi.fn(),
    selectNode: vi.fn(),
    selectDefaultNode: vi.fn(),
    drillInto: vi.fn(),
    backToOverview: vi.fn(),
  } as unknown as TaskPanelNav;
}
const task = {
  nodes: [
    { id: 'first', name: '第一棒', runMode: 'single_bot', sessionId: 'first-session', assignee: 'first-bot' },
    { id: 'second', name: '第二棒', runMode: 'coop_group', sessionId: 'second-group-session', groupId: 'second-group' },
  ],
  mainSessionId: 'main-session', sourceGroupId: null, ownerBotId: 'owner-bot',
} as unknown as TaskView;

it('restores the host origin when the root node is selected', () => {
  const record = nav();
  render(<NodeListViewV2 task={task} nav={record} />);
  fireEvent.click(screen.getByRole('button', { name: '选中节点 第1棒 第一棒' }));
  expect(record.selectRootNode).toHaveBeenCalledWith('first');
  expect(record.selectNode).not.toHaveBeenCalled();
});

it('keeps the original-session fallback when the first node has no resolvable target', () => {
  const record = nav();
  const noTarget = { ...task, mainSessionId: null, nodes: [{ id: 'first', name: '第一棒' }] } as unknown as TaskView;
  render(<NodeListViewV2 task={noTarget} nav={record} />);
  fireEvent.click(screen.getByRole('button', { name: '选中节点 第1棒 第一棒' }));
  expect(record.selectRootNode).toHaveBeenCalledWith('first');
  expect(record.selectNode).not.toHaveBeenCalled();
});

it.each(['first-session', 'bcs_grp_third:s3'])('first-node navigation restores the host origin %s', async entry => {
  const host = makeHost();
  host.getConversation = () => ({ sessionId: entry, kind: entry.startsWith('bcs_grp_') ? 'group' : 'bot' });
  host.captureOrigin = () => ({ sessionId: entry });
  const controller = createTaskPanelController(host);
  try {
    controller.openTask({ id: 'task-t1', title: 'Task', type: 'taskPanel.TaskLoopView', closable: true, params: { taskId: 't1' } }, 'explicit');
    render(<NodeListViewV2 task={task} nav={controller.nav('t1')} />);
    fireEvent.click(screen.getByRole('button', { name: '选中节点 第1棒 第一棒' }));
    await Promise.resolve();
    expect(host.restoreOrigin).toHaveBeenCalledWith({ sessionId: entry }, expect.anything());
    expect(host.navigate).not.toHaveBeenCalled();
    expect(controller.getSnapshot().activeTaskId).toBe('t1');
    expect(controller.nav('t1').selectedNodeId).toBe('first');
    expect(host.recordInteraction).not.toHaveBeenCalled();
  } finally { controller.dispose(); }
});
