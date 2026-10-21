/** @jest-environment node */
import { beforeEach, describe, expect, it } from '@jest/globals';

import { useTaskSidePanelStore, type TaskNavTarget } from '@/stores/taskSidePanelStore';

const target = (sessionId: string, extra?: Partial<TaskNavTarget>): TaskNavTarget => ({
  taskId: 't1',
  sessionId,
  kind: 'bot',
  ...extra,
});

describe('taskSidePanelStore', () => {
  beforeEach(() => useTaskSidePanelStore.getState().resetAll());

  it('初始态:无激活任务、导航分片为空、无待消费跳转', () => {
    const s = useTaskSidePanelStore.getState();
    expect(s.activeTaskId).toBeNull();
    expect(s.navByTask).toEqual({});
    expect(s.pendingNavigation).toBeNull();
    expect(s.nodeScrollSignal).toBe(0);
    expect(s.linkedSession).toBeNull();
  });

  it('selectNode 更新选中、发出跳转并 +1 滚动信号', () => {
    const s = useTaskSidePanelStore.getState();
    s.selectNode('t1', 'n2', target('sess-main'));

    const after = useTaskSidePanelStore.getState();
    expect(after.navByTask['t1']?.selectedNodeId).toBe('n2');
    expect(after.pendingNavigation?.sessionId).toBe('sess-main');
    expect(after.pendingNavigation?.taskId).toBe('t1');
    expect(after.nodeScrollSignal).toBe(1);
  });

  it('selectNode 携带 Row1 字段(kind/groupId/botId/时间窗)透传进 pendingNavigation', () => {
    useTaskSidePanelStore.getState().selectNode(
      't1',
      'n3',
      target('sess-grp', {
        kind: 'group',
        groupId: 'g902',
        nodeId: 'n3',
        startTime: 1700000000000,
        endTime: 1700000005000,
      }),
    );

    const nav = useTaskSidePanelStore.getState().pendingNavigation;
    expect(nav).toMatchObject({
      sessionId: 'sess-grp',
      kind: 'group',
      groupId: 'g902',
      nodeId: 'n3',
      startTime: 1700000000000,
      endTime: 1700000005000,
    });
  });

  it('selectNode 传 null target 时选中但不发跳转', () => {
    useTaskSidePanelStore.getState().selectNode('t1', 'n2', null);

    const after = useTaskSidePanelStore.getState();
    expect(after.navByTask['t1']?.selectedNodeId).toBe('n2');
    expect(after.pendingNavigation).toBeNull();
    expect(after.nodeScrollSignal).toBe(1);
  });

  it('drillInto 去重 push、封顶 2 层、不增加滚动信号', () => {
    const s = useTaskSidePanelStore.getState();
    s.drillInto('t1', 'n2', target('sess-grp'));
    s.drillInto('t1', 'n2', null); // 去重
    s.drillInto('t1', 'n2v', null);
    s.drillInto('t1', 'n3', null); // 封顶,忽略

    const after = useTaskSidePanelStore.getState();
    expect(after.navByTask['t1']?.drillStack).toEqual(['n2', 'n2v']);
    expect(after.nodeScrollSignal).toBe(0);
  });

  it('drillInto 同样发出跳转(语义=selectNode+进栈)', () => {
    useTaskSidePanelStore.getState().drillInto('t1', 'n2', target('sess-grp'));

    expect(useTaskSidePanelStore.getState().pendingNavigation?.sessionId).toBe('sess-grp');
  });

  it('backToOverview 清空下钻栈但保留选中', () => {
    useTaskSidePanelStore.getState().selectNode('t1', 'n2', target('sess-grp'));
    useTaskSidePanelStore.getState().drillInto('t1', 'n2', null);
    useTaskSidePanelStore.getState().backToOverview('t1');

    const rec = useTaskSidePanelStore.getState().navByTask['t1'];
    expect(rec?.drillStack).toEqual([]);
    expect(rec?.selectedNodeId).toBe('n2');
  });

  it('consumePendingNavigation 置空待消费跳转', () => {
    useTaskSidePanelStore.getState().selectNode('t1', 'n2', target('sess-main'));
    useTaskSidePanelStore.getState().consumePendingNavigation();

    expect(useTaskSidePanelStore.getState().pendingNavigation).toBeNull();
  });

  it('navByTask 按 task 分片隔离:taskA 下钻不影响 taskB(Review Focus #5)', () => {
    useTaskSidePanelStore.getState().drillInto('tA', 'nA1', null);
    useTaskSidePanelStore.getState().selectNode('tA', 'nA2', null);

    useTaskSidePanelStore.getState().drillInto('tB', 'nB1', null);

    const s = useTaskSidePanelStore.getState();
    expect(s.navByTask['tA']?.drillStack).toEqual(['nA1']);
    expect(s.navByTask['tA']?.selectedNodeId).toBe('nA2');
    expect(s.navByTask['tB']?.selectedNodeId).toBeNull();
    expect(s.navByTask['tB']?.drillStack).toEqual(['nB1']);
  });

  it('setActiveTask 切换激活任务并清掉待消费跳转,不动下钻分片', () => {
    useTaskSidePanelStore.getState().setActiveTask('t1');
    useTaskSidePanelStore.getState().drillInto('t1', 'n2', null);
    useTaskSidePanelStore.getState().selectNode('t1', 'n2', target('sess-main'));

    useTaskSidePanelStore.getState().setActiveTask('t2');

    const s = useTaskSidePanelStore.getState();
    expect(s.activeTaskId).toBe('t2');
    expect(s.pendingNavigation).toBeNull();
    expect(s.navByTask['t1']?.drillStack).toEqual(['n2']);
  });

  it('resetAll 回到初始态', () => {
    useTaskSidePanelStore.getState().setActiveTask('t1');
    useTaskSidePanelStore.getState().drillInto('t1', 'n2', target('sess-main'));

    useTaskSidePanelStore.getState().resetAll();

    const s = useTaskSidePanelStore.getState();
    expect(s.activeTaskId).toBeNull();
    expect(s.navByTask).toEqual({});
    expect(s.pendingNavigation).toBeNull();
    expect(s.nodeScrollSignal).toBe(0);
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
