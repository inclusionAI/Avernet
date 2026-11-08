/** @jest-environment node */
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { beforeEach, describe, expect, it } from '@jest/globals';

const tab = (taskId: string) => ({
  id: `task-${taskId}`,
  title: `Task ${taskId}`,
  type: 'taskPanel.TaskLoopView',
  params: { taskId },
  closable: true,
});

describe('taskConversationNavigationStore', () => {
  beforeEach(() => useTaskConversationNavigationStore.getState().resetAll());

  it('每个 task 只记录首次宿主会话，节点间跳转不会覆盖原点', () => {
    const store = useTaskConversationNavigationStore.getState();
    store.rememberOrigin('t1', {
      routeUrl: '/workspace/chat?bot=b1&session=s1',
      sessionId: 's1',
      kind: 'bot',
      botId: 'b1',
    });
    store.rememberOrigin('t1', {
      routeUrl: '/workspace/collaboration?group=g2&session=s2',
      sessionId: 's2',
      kind: 'group',
      groupId: 'g2',
    });

    expect(useTaskConversationNavigationStore.getState().originsByTask.t1.sessionId).toBe('s1');
  });

  it('保存可跨路由重建的 TaskPanel tab 快照', () => {
    useTaskConversationNavigationStore.getState().rememberTaskTab('t1', {
      ...tab('t1'),
      eventEmitter: { on: () => () => {}, emit: () => {}, removeAllListeners: () => {} },
    });

    expect(useTaskConversationNavigationStore.getState().tabsByTask.t1).toEqual(tab('t1'));
  });

  it('begin → route committed → route hydrated → complete 生命周期按 navigation id 防止旧回调误改新导航', () => {
    const store = useTaskConversationNavigationStore.getState();
    store.rememberTaskTab('t1', tab('t1'));
    const first = store.beginNavigation('t1', '/workspace/chat', 's1');
    store.markRouteCommitted(first);
    expect(useTaskConversationNavigationStore.getState().navigation).toMatchObject({
      id: first,
      taskId: 't1',
      sessionId: 's1',
      routeCommitted: true,
      routeHydrated: false,
      tab: tab('t1'),
    });
    store.markRouteHydrated(first);
    expect(useTaskConversationNavigationStore.getState().navigation?.routeHydrated).toBe(true);

    const second = useTaskConversationNavigationStore
      .getState()
      .beginNavigation('t1', '/workspace/collaboration', 's2');
    useTaskConversationNavigationStore.getState().markRouteHydrated(first);
    expect(useTaskConversationNavigationStore.getState().navigation).toMatchObject({
      id: second,
      routeCommitted: false,
      routeHydrated: false,
    });
    useTaskConversationNavigationStore.getState().completeNavigation(first);
    expect(useTaskConversationNavigationStore.getState().navigation?.id).toBe(second);
    useTaskConversationNavigationStore.getState().markRouteCommitted(second);
    useTaskConversationNavigationStore.getState().markRouteHydrated(second);
    expect(useTaskConversationNavigationStore.getState().navigation).toMatchObject({
      id: second,
      routeCommitted: true,
      routeHydrated: true,
    });
    useTaskConversationNavigationStore.getState().completeNavigation(second);
    expect(useTaskConversationNavigationStore.getState().navigation).toBeNull();
  });

  it('不同任务的 origin/tab 相互隔离', () => {
    const store = useTaskConversationNavigationStore.getState();
    store.rememberTaskTab('tA', tab('tA'));
    store.rememberTaskTab('tB', tab('tB'));
    store.rememberOrigin('tA', { routeUrl: '/workspace/chat?session=a', sessionId: 'a', kind: 'bot' });
    store.rememberOrigin('tB', { routeUrl: '/workspace/chat?session=b', sessionId: 'b', kind: 'bot' });

    const state = useTaskConversationNavigationStore.getState();
    expect(state.tabsByTask.tA.id).toBe('task-tA');
    expect(state.tabsByTask.tB.id).toBe('task-tB');
    expect(state.originsByTask.tA.sessionId).toBe('a');
    expect(state.originsByTask.tB.sessionId).toBe('b');
  });
});
