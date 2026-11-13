/** @jest-environment node */
import { beforeEach, describe, expect, it } from '@jest/globals';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';

describe('taskSidePanelStore host projection and explicit intent', () => {
  beforeEach(() => useTaskSidePanelStore.getState().resetAll());
  it('starts without displayed task, intent or linked container', () => {
    expect(useTaskSidePanelStore.getState()).toMatchObject({ activeTaskId: null, taskPanelOpenIntentTaskId: null, linkedSession: null });
  });
  it('has no task-domain commands or navigation state to maintain beside the controller', () => {
    const state = useTaskSidePanelStore.getState();
    for (const key of ['navByTask', 'pendingNavigation', 'nodeScrollSignal', 'setActiveTask', 'clearActiveTask',
      'selectNode', 'selectRootNode', 'drillInto', 'backToOverview', 'requestTaskNavigation', 'consumePendingNavigation']) {
      expect(state).not.toHaveProperty(key);
    }
  });
  it('marking or consuming intent never changes the displayed-task projection', () => {
    useTaskSidePanelStore.setState({ activeTaskId: 'actually-mounted' }); // read-only projection fixture
    const store = useTaskSidePanelStore.getState();
    store.markTaskPanelOpenIntent('requested');
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('actually-mounted');
    expect(store.consumeTaskPanelOpenIntent('other')).toBe(false);
    expect(useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId).toBe('requested');
    expect(store.consumeTaskPanelOpenIntent('requested')).toBe(true);
    expect(store.consumeTaskPanelOpenIntent('requested')).toBe(false);
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('actually-mounted');
  });
  it('reset clears projection, intent and host container without retaining a task graph', () => {
    useTaskSidePanelStore.setState({ activeTaskId: 'mounted' });
    const store = useTaskSidePanelStore.getState();
    store.markTaskPanelOpenIntent('requested'); store.openLinkedSession({ sessionId: 'linked', kind: 'bot' });
    store.resetAll();
    expect(useTaskSidePanelStore.getState()).toMatchObject({ activeTaskId: null, taskPanelOpenIntentTaskId: null, linkedSession: null });
  });
});
describe('taskSidePanelStore.linkedSession(任务节点会话联动态)', () => {
  beforeEach(() => useTaskSidePanelStore.getState().resetAll());

  it('openLinkedSession 记录群会话目标;closeLinkedSession 清除', () => {
    const store = useTaskSidePanelStore.getState();
    store.openLinkedSession({
      sessionId: 'bcs_grp_902:round:2',
      kind: 'group',
      groupId: 'g-902',
      botId: null,
    });

    expect(useTaskSidePanelStore.getState().linkedSession).toEqual({
      sessionId: 'bcs_grp_902:round:2',
      kind: 'group',
      groupId: 'g-902',
      botId: null,
    });

    useTaskSidePanelStore.getState().closeLinkedSession();
    expect(useTaskSidePanelStore.getState().linkedSession).toBeNull();
  });

  it('openLinkedSession 记录 Bot 会话目标', () => {
    useTaskSidePanelStore.getState().openLinkedSession({
      sessionId: 'agent:main:session:s-1',
      kind: 'bot',
      groupId: null,
      botId: 'bot-9',
    });

    expect(useTaskSidePanelStore.getState().linkedSession).toEqual({
      sessionId: 'agent:main:session:s-1',
      kind: 'bot',
      groupId: null,
      botId: 'bot-9',
    });
  });

  it('resetAll 清除联动会话', () => {
    useTaskSidePanelStore.getState().openLinkedSession({
      sessionId: 'bcs_grp_x:1',
      kind: 'group',
      groupId: null,
    });
    useTaskSidePanelStore.getState().resetAll();
    expect(useTaskSidePanelStore.getState().linkedSession).toBeNull();
  });
});
