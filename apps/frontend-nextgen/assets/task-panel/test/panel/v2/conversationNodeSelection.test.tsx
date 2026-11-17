/** @vitest-environment jsdom */
import React, { useSyncExternalStore } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ConversationRef } from '../../../src/contracts';
import { createTaskPanelController, type TaskPanelController } from '../../../src/controller/createTaskPanelController';
import { TaskPanelHostContext } from '../../../src/panel/hostContext';
import type { TaskView } from '../../../src/panel/types';
import { TaskPanelV2 } from '../../../src/panel/v2/TaskPanelV2';
import { makeHost, tab } from '../taskSwitcherFixtures';

vi.mock('../../../src/panel/hooks/useGroupMemberNames', () => ({ useGroupMemberNames: () => ({}) }));
const task = {
  id: 't1', name: '接力任务', status: 'DONE', taskType: 'dynamic', loopRound: 1,
  progress: { total: 3, done: 3, running: 0, percent: 100 },
  artifacts: [], dagNodes: [], dagEdges: [], mainSessionId: 'first-session',
  sourceGroupId: null, ownerBotId: 'first-bot', derivedSessionIds: [],
  nodes: [
    { id: 'first', name: '第一棒', runMode: 'single_bot', sessionId: 'first-session', assignee: 'first-bot' },
    { id: 'second', name: '第二棒', runMode: 'coop_group', sessionId: 'bcs_grp_second:s2', groupId: 'bcs_grp_second' },
    { id: 'third', name: '第三棒', runMode: 'coop_group', sessionId: 'bcs_grp_third:s3', groupId: 'bcs_grp_third' },
  ].map((node, index) => ({ ...node, sequence: index + 1, status: 'done', artifacts: [], stepTraces: [], hasSubTask: false })),
} as unknown as TaskView;
const controllers: TaskPanelController[] = [];
afterEach(() => { act(() => controllers.forEach(controller => controller.dispose())); controllers.length = 0; });
async function setup(initial: ConversationRef) {
  let conversation = initial;
  const host = makeHost();
  host.getConversation = () => conversation;
  const controller = createTaskPanelController(host);
  controllers.push(controller);
  await controller.enterConversation(conversation, tab('t1'), { recover: false });
  function View({ value = task }: { value?: TaskView }) {
    useSyncExternalStore(controller.subscribe, controller.getSnapshot);
    return <TaskPanelHostContext.Provider value={host}>
      <TaskPanelV2 task={value} nav={controller.nav('t1')} apiBaseUrl="" />
    </TaskPanelHostContext.Provider>;
  }
  return { host, controller, View, async enter(next: ConversationRef) {
    conversation = next;
    await controller.enterConversation(next, tab('t1'), { recover: false });
  } };
}
it.each([
  ['second', 'bcs_grp_second:s2', 'bcs_grp_second'],
  ['third', 'bcs_grp_third:s3', 'bcs_grp_third'],
])('silently highlights %s for the current collaboration session', async (nodeId, sessionId, groupId) => {
  const { host, controller, View } = await setup({ sessionId, kind: 'group', groupId });
  render(<View />);
  expect(controller.nav('t1').selectedNodeId).toBe(nodeId);
  expect(host.navigate).not.toHaveBeenCalled();
  expect(host.restoreOrigin).not.toHaveBeenCalled();
  expect(host.recordInteraction).not.toHaveBeenCalled();
});
it('falls back to the first node, not another session in the same group', async () => {
  const { controller, View } = await setup({ sessionId: 'bcs_grp_second:other', kind: 'group', groupId: 'bcs_grp_second' });
  render(<View />);
  expect(controller.nav('t1').selectedNodeId).toBe('first');
});
it('does not overwrite explicit selection during refresh in the same conversation', async () => {
  const { controller, View } = await setup({ sessionId: 'bcs_grp_second:s2', kind: 'group' });
  const view = render(<View />);
  fireEvent.click(screen.getByRole('button', { name: '选中节点 第3棒 第三棒' }));
  view.rerender(<View value={{ ...task }} />);
  expect(controller.nav('t1').selectedNodeId).toBe('third');
});
it('reselects for an externally opened conversation instead of keeping the previous task selection', async () => {
  const { controller, View, enter } = await setup({ sessionId: 'first-session', kind: 'bot' });
  render(<View />);
  expect(controller.nav('t1').selectedNodeId).toBe('first');
  await act(() => enter({ sessionId: 'bcs_grp_third:s3', kind: 'group' }));
  expect(controller.nav('t1').selectedNodeId).toBe('third');
});
it('prefers an explicit node session over an earlier node fallback', async () => {
  const { controller, View } = await setup({ sessionId: 'bcs_grp_second:s2', kind: 'group' });
  render(<View value={{ ...task, mainSessionId: 'bcs_grp_second:s2', nodes: task.nodes.map((node, index) => index === 0 ? { ...node, sessionId: null } : node) }} />);
  expect(controller.nav('t1').selectedNodeId).toBe('second');
});
it('resolves legacy session metadata when the node has no explicit session', async () => {
  const { controller, View } = await setup({ sessionId: 'legacy-group-session', kind: 'group' });
  render(<View value={{ ...task, derivedSessionIds: ['legacy-group-session'], nodes: task.nodes.map(node => node.id === 'second' ? { ...node, sessionId: null } : node) }} />);
  expect(controller.nav('t1').selectedNodeId).toBe('second');
});
it('preserves the clicked node during a task navigation handoff even when nodes share a session', async () => {
  const { host, controller, View, enter } = await setup({ sessionId: 'first-session', kind: 'bot' });
  host.navigate = vi.fn(async () => {
    await enter({ sessionId: 'bcs_grp_second:s2', kind: 'group' });
    return { status: 'ready' as const };
  });
  render(<View value={{ ...task, nodes: task.nodes.map(node => node.id === 'third' ? { ...node, sessionId: 'bcs_grp_second:s2', groupId: 'bcs_grp_second' } : node) }} />);
  fireEvent.click(screen.getByRole('button', { name: '选中节点 第3棒 第三棒' }));
  await waitFor(() => expect(controller.getSnapshot().conversation?.sessionId).toBe('bcs_grp_second:s2'));
  expect(controller.nav('t1').selectedNodeId).toBe('third');
  expect(controller.getSnapshot().activeTaskId).toBe('t1');
  expect(host.navigate).toHaveBeenCalledTimes(1);
  expect(host.recordInteraction).not.toHaveBeenCalled();
});
