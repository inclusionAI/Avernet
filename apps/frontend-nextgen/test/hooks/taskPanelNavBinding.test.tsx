/** @jest-environment jsdom */
import { useTaskPanelNavBinding } from '@/hooks/taskPanelNavBinding';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { beforeEach, describe, expect, it } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

describe('useTaskPanelNavBinding(taskId)', () => {
  beforeEach(() => {
    useTaskSidePanelStore.getState().resetAll();
    useTaskConversationNavigationStore.getState().resetAll();
    window.history.pushState({}, '', '/workspace/chat?bot=bot-host&session=s-host');
  });

  it('缝合 store 分片:读选中/下钻栈,未初始化时给出空缺省', () => {
    const { result } = renderHook(() => useTaskPanelNavBinding('t1'));
    expect(result.current.selectedNodeId).toBeNull();
    expect(result.current.drillStack).toEqual([]);
  });

  it('回调闭环写回对应 task 分片并联动 store', () => {
    const { result } = renderHook(() => useTaskPanelNavBinding('t1'));

    act(() => result.current.selectNode('n2', { sessionId: 'sess-main', kind: 'bot' }));
    act(() => result.current.drillInto('n2', { sessionId: 'sess-grp', kind: 'group', groupId: 'g902' }));

    const s = useTaskSidePanelStore.getState();
    expect(s.navByTask['t1']?.selectedNodeId).toBe('n2');
    expect(s.navByTask['t1']?.drillStack).toEqual(['n2']);
    expect(s.pendingNavigation?.sessionId).toBe('sess-grp');

    act(() => result.current.backToOverview());
    expect(useTaskSidePanelStore.getState().navByTask['t1']?.drillStack).toEqual([]);
  });

  it('根节点回调绑定当前 task，并退出任务联动会话', () => {
    const { result } = renderHook(() => useTaskPanelNavBinding('t1'));
    useTaskSidePanelStore.getState().openLinkedSession({ sessionId: 'sess-other', kind: 'bot', botId: 'bot-9' });

    act(() => result.current.selectRootNode('n-root'));

    const s = useTaskSidePanelStore.getState();
    expect(s.navByTask['t1']?.selectedNodeId).toBe('n-root');
    expect(s.pendingNavigation).toBeNull();
    expect(s.linkedSession).toBeNull();
  });

  it('根节点在联动会话中恢复任务最初的宿主 URL', () => {
    useTaskConversationNavigationStore.getState().rememberOrigin('t1', {
      routeUrl: '/workspace/chat?bot=bot-host&session=s-host',
      sessionId: 's-host',
      kind: 'bot',
      botId: 'bot-host',
    });
    window.history.pushState({}, '', '/workspace/collaboration?group=g2&session=s-linked');
    const { result } = renderHook(() => useTaskPanelNavBinding('t1'));

    act(() => result.current.selectRootNode('n-root'));

    expect(useTaskSidePanelStore.getState().pendingNavigation).toMatchObject({
      taskId: 't1',
      sessionId: 's-host',
      routeUrl: '/workspace/chat?bot=bot-host&session=s-host',
    });
  });

  it('根节点在原宿主会话中保持当前内容不变，即使 URL 已被规范化', () => {
    useTaskConversationNavigationStore.getState().rememberOrigin('t1', {
      routeUrl: '/workspace/chat?bot=bot-host&session=s-host',
      sessionId: 's-host',
      kind: 'bot',
      botId: 'bot-host',
    });
    window.history.pushState(
      {},
      '',
      '/workspace/chat?section=managed&bot=bot-host&origin=mine&session=s-host#latest',
    );
    const { result } = renderHook(() => useTaskPanelNavBinding('t1'));

    act(() => result.current.selectRootNode('n-root'));

    expect(useTaskSidePanelStore.getState().pendingNavigation).toBeNull();
  });

  it('回调在 taskId 间不串场(闭包绑定实例 taskId)', () => {
    const a = renderHook(() => useTaskPanelNavBinding('tA')).result;
    const b = renderHook(() => useTaskPanelNavBinding('tB')).result;

    act(() => a.current.selectNode('nA1', null));
    act(() => b.current.selectNode('nB1', null));

    const s = useTaskSidePanelStore.getState();
    expect(s.navByTask['tA']?.selectedNodeId).toBe('nA1');
    expect(s.navByTask['tB']?.selectedNodeId).toBe('nB1');
  });
});
