/** @jest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { PanelHandle } from '@tc-chat/core';
import { configureTaskPanelSession, getTaskPanelController, resetTaskPanelRuntime } from '@/services/tasks/panel/taskPanelRuntime';
import { onTaskPanelTabClosed, taskNavigationAwareHandle } from '@/services/tasks/panel/taskPanelHandleAdapter';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';

jest.mock('@/capabilities', () => ({ getCapabilities: () => ({ getHumanIdentity: () => ({ status: 'available', value: { userId: 'viewer' } }) }) }));
jest.mock('@/services/tasks/panel/taskPanelAssetLoader', () => ({ loadTaskPanelAsset: async () => require('../../../../assets/task-panel/src/controller/createTaskPanelController') }));
jest.mock('@/services/tasks/panel/taskPanelArtifactAdapter', () => ({ taskPanelArtifacts: {} }));
jest.mock('@/services/workspace/chatBridge', () => ({ chatBridge: { openPanelTab: jest.fn() } }));

beforeEach(() => { resetTaskPanelRuntime(); window.history.pushState({}, '', '/workspace/chat?bot=b1&session=s1'); });
afterEach(() => resetTaskPanelRuntime());
const tab = (id: string) => ({ id: `task-${id}`, type: 'taskPanel.TaskLoopView', title: id, closable: true, params: { taskId: id } });
async function setup() {
  let signal: AbortSignal | undefined;
  configureTaskPanelSession({ sessionId: 's1', kind: 'bot' }, {
    history: { readPage: async () => ({ messages: [], nextCursor: null }) },
    navigate: async (_target, pending) => { signal = pending; return new Promise(resolve => {
      pending.addEventListener('abort', () => resolve({ status: 'cancelled', reason: 'closed' }), { once: true });
    }); },
  });
  const c = await getTaskPanelController();
  await c.enterConversation({ sessionId: 's1', kind: 'bot' }, tab('t1'), { recover: false });
  const raw = { openTab: jest.fn(), closeTab: jest.fn(), updateTab: jest.fn(), focusTab: jest.fn(), openPanel: jest.fn(),
    closePanel: jest.fn(), closePanelForce: jest.fn(), flushContext: jest.fn(), flushInteractions: jest.fn() };
  return { c, raw, handle: taskNavigationAwareHandle(raw as unknown as PanelHandle), signal: () => signal };
}
describe('SDK close/open operations use the real shared controller, not host task setters', () => {
  it('ordinary force-close leaves the conversation and aborts navigation, keeping unrelated explicit intent', async () => {
    const { c, raw, handle, signal } = await setup();
    c.nav('t1').selectNode('selected', { sessionId: 'linked', kind: 'bot' });
    useTaskSidePanelStore.getState().markTaskPanelOpenIntent('next-explicit');
    handle.closePanelForce();
    expect(signal()?.aborted).toBe(true); expect(c.getSnapshot()).toMatchObject({ conversation: null, activeTaskId: null });
    expect(useTaskSidePanelStore.getState()).toMatchObject({ activeTaskId: null, taskPanelOpenIntentTaskId: 'next-explicit' });
    expect(raw.closePanelForce).toHaveBeenCalledTimes(1);
  });
  it('preserved navigation suppresses SDK force-close rather than clearing shared focus', async () => {
    const { c, raw, handle } = await setup();
    const store = useTaskConversationNavigationStore.getState();
    const id = store.beginNavigation('t1', '/workspace/chat', 's1'); store.markRouteCommitted(id); store.markRouteHydrated(id);
    handle.closePanelForce();
    expect(raw.closePanelForce).not.toHaveBeenCalled(); expect(c.getSnapshot().activeTaskId).toBe('t1');
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('t1');
  });
  it('SDK tab-X callback closes only the active task and aborts pending navigation', async () => {
    const { c, signal } = await setup(); c.nav('t1').selectNode('selected', { sessionId: 'child', kind: 'group' });
    onTaskPanelTabClosed('task-other'); expect(c.getSnapshot().activeTaskId).toBe('t1');
    onTaskPanelTabClosed('task-t1'); expect(c.getSnapshot().activeTaskId).toBeNull();
    expect(useTaskSidePanelStore.getState().activeTaskId).toBeNull(); expect(signal()?.aborted).toBe(true);
  });
  it('imperative closeTab uses the same acknowledgement and forwards the SDK id', async () => {
    const { c, handle, raw } = await setup(); handle.closeTab('task-t1');
    expect(c.getSnapshot().activeTaskId).toBeNull(); expect(raw.closeTab).toHaveBeenCalledWith('task-t1');
  });
  it('closing a noncanonical remembered SDK tab closes its active task too', async () => {
    const { c } = await setup();
    useTaskConversationNavigationStore.getState().rememberTaskTab('t1', { ...tab('t1'), id: 'sdk-tab-alias' });
    onTaskPanelTabClosed('sdk-tab-alias'); expect(c.getSnapshot().activeTaskId).toBeNull();
  });
  it('collapse cancels navigation but retains task/node so reopening does not invent another selection', async () => {
    const { c, handle, raw, signal } = await setup(); c.nav('t1').selectNode('selected', { sessionId: 'child', kind: 'bot' });
    handle.closePanel();
    expect(signal()?.aborted).toBe(true); expect(c.getSnapshot().activeTaskId).toBe('t1');
    expect(c.nav('t1').selectedNodeId).toBe('selected'); expect(raw.closePanel).toHaveBeenCalledTimes(1);
  });
  it('explicit one-shot open intent permits SDK open during a lease without pretending the new tab is displayed', async () => {
    const { c, handle, raw } = await setup(); const store = useTaskConversationNavigationStore.getState();
    const id = store.beginNavigation('t1', '/workspace/chat', 's1'); store.markRouteCommitted(id);
    handle.openTab(tab('history')); expect(raw.openTab).not.toHaveBeenCalled();
    useTaskSidePanelStore.getState().markTaskPanelOpenIntent('explicit');
    handle.openTab(tab('explicit')); handle.openTab(tab('explicit'));
    expect(raw.openTab).toHaveBeenCalledTimes(1); expect(raw.openTab).toHaveBeenCalledWith(tab('explicit'));
    expect(c.getSnapshot().activeTaskId).toBe('t1'); expect(useTaskSidePanelStore.getState().activeTaskId).toBe('t1');
  });
});
