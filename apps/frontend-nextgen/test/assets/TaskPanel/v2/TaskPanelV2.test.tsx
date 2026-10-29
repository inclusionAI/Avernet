/** @jest-environment jsdom */
// TaskPanelV2 壳:面包屑两级栈 + 收起按钮 + nav 注入/本地降级(spec §3.2)。
import type { ResolvedSessionTarget } from '@/assets/TaskPanel/sessionResolution';
import { fetchGroupDetail } from '@/assets/TaskPanel/GroupDrillDown';
import type { TaskView } from '@/assets/TaskPanel/types';
import { TaskPanelV2, type TaskPanelV2Props } from '@/assets/TaskPanel/v2/TaskPanelV2';
import { useLocalTaskPanelNav, type TaskPanelNav } from '@/assets/TaskPanel/v2/taskPanelNav';
import { beforeEach, describe, expect, it } from '@jest/globals';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';

jest.mock('@/assets/TaskPanel/GroupDrillDown', () => {
  const actual = jest.requireActual('@/assets/TaskPanel/GroupDrillDown');
  return {
    ...actual,
    fetchGroupDetail: jest.fn(),
  };
});

const baseTask = {
  id: 't1',
  name: '竞品分析',
  description: '',
  goal: '',
  objective: '',
  acceptances: [],
  status: 'EXECUTING',
  taskType: 'dynamic',
  taskTypeLabel: '动态',
  sourceLabel: '',
  ownerBotName: '规划师',
  ownerBotId: 'bot-1',
  ownerUserId: 'user-1',
  createdAt: '',
  finishedAt: null,
  loopRound: 1,
  needsAttention: false,
  progress: { total: 2, pending: 0, planning: 0, running: 1, done: 1, failed: 0, hung: 0, skipped: 0, percent: 50 },
  artifacts: [],
  derivedSessionIds: [],
  nodes: [
    {
      id: 'n-solo',
      name: '规划与澄清',
      sequence: 1,
      status: 'done',
      present: 'merged',
      hasSubTask: false,
      artifacts: [],
      stepTraces: [],
    },
    {
      id: 'n-group',
      name: '并行调研执行协作群节点',
      sequence: 2,
      status: 'running',
      present: 'running',
      drillable: true,
      hasSubTask: false,
      artifacts: [],
      stepTraces: [],
    },
  ],
  dagNodes: [
    { id: 'n-solo', label: '规划与澄清', status: 'done', x: 0, y: 0, isCurrent: false, short: '规划与澄清' },
    {
      id: 'n-group',
      label: '并行调研执行协作群节点',
      status: 'running',
      x: 0,
      y: 1,
      isCurrent: false,
      short: '并行调研执…',
    },
  ],
  dagEdges: [],
} as unknown as TaskView;

const makeNav = (): TaskPanelNav & {
  calls: { root: string[]; select: string[]; default: string[]; drill: string[]; back: number[] };
} => {
  const calls = {
    root: [] as string[],
    select: [] as string[],
    default: [] as string[],
    drill: [] as string[],
    back: [] as number[],
  };
  return {
    selectedNodeId: null,
    drillStack: [],
    calls,
    selectRootNode: (id: string) => calls.root.push(id),
    selectNode: (id: string) => calls.select.push(id),
    selectDefaultNode: (id: string) => calls.default.push(id),
    drillInto: (id: string) => calls.drill.push(id),
    backToOverview: () => calls.back.push(1),
  } as unknown as TaskPanelNav & { calls: typeof calls };
};

const baseProps = (overrides?: Partial<TaskPanelV2Props>): TaskPanelV2Props => ({
  task: baseTask,
  nav: makeNav(),
  apiBaseUrl: '',
  ...overrides,
});

describe('TaskPanelV2 壳', () => {
  it('任务数据可用后默认调用第一节点选中，不触发会话跳转回调', () => {
    const nav = makeNav();
    render(<TaskPanelV2 {...baseProps({ nav })} />);

    expect(nav.calls.default).toEqual(['n-solo']);
    expect(nav.calls.select).toEqual([]);
    expect(nav.calls.root).toEqual([]);
  });

  it('L1 默认渲染焦点任务状态条:任务名 + 任务状态,L1 内容区可见', () => {
    render(<TaskPanelV2 {...baseProps()} />);
    expect(screen.queryByTitle('焦点任务')).toBeNull();
    expect(screen.getByText('竞品分析')).toBeTruthy();
    expect(screen.getByText('执行中')).toBeTruthy();
    expect(screen.getByTitle('任务总览：竞品分析')).toBeTruthy();
  });

  it('收起按钮:注入 onTogglePanel 时渲染并回调;未注入不渲染(Ruling 6)', () => {
    const onTogglePanel = jest.fn();
    const { unmount } = render(<TaskPanelV2 {...baseProps({ onTogglePanel })} />);
    const btn = screen.getByTitle('收起任务副屏');
    fireEvent.click(btn);
    expect(onTogglePanel).toHaveBeenCalledTimes(1);
    unmount();

    render(<TaskPanelV2 {...baseProps()} />);
    expect(screen.queryByTitle('收起任务副屏')).toBeNull();
  });

  it('下钻后切换 L2:面包屑 = 任务总览 › 节点短名,且「任务总览」变为可点按钮', () => {
    const nav = makeNav();
    nav.drillStack = ['n-group'];
    render(<TaskPanelV2 {...baseProps({ nav })} />);
    expect(screen.getByText('并行调研执…')).toBeTruthy();
    // 当前用 dagNodes.short 截断的短标题
    expect(screen.getByRole('button', { name: '任务总览' })).toBeTruthy();
  });

  it('面包屑返回:点击「任务总览」调用 backToOverview', () => {
    const nav = makeNav();
    nav.drillStack = ['n-group'];
    render(<TaskPanelV2 {...baseProps({ nav })} />);
    fireEvent.click(screen.getByRole('button', { name: '任务总览' }));
    expect(nav.calls.back).toHaveLength(1);
  });

  it('drillStack 顶节点在下一次渲染已不存在(数据变更)→ L2 空态而非崩溃', () => {
    const nav = makeNav();
    nav.drillStack = ['n-gone'];
    render(<TaskPanelV2 {...baseProps({ nav })} />);
    expect(screen.getByText(/节点数据缺失/)).toBeTruthy();
  });

  it('多任务切换器槽位渲染在焦点任务状态条上方', () => {
    render(<TaskPanelV2 {...baseProps({ switcher: <div data-testid="switcher-bar">bar</div> })} />);
    const switcherNode = screen.getByTestId('switcher-bar');
    const overviewTitle = screen.getByTitle('任务总览：竞品分析');
    expect(switcherNode.compareDocumentPosition(overviewTitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('未注入多任务切换器时顶部仍直接展示焦点任务状态条', () => {
    render(<TaskPanelV2 {...baseProps()} />);
    const overviewTitle = screen.getByTitle('任务总览：竞品分析');
    expect(overviewTitle).toBeTruthy();
    expect(overviewTitle.closest('[data-task-panel-root]')?.getAttribute('data-task-panel-root')).toBe('true');
  });
});

describe('TaskPanelV2 协作群成员名展示', () => {
  it('步骤执行体与产物产出者显示群成员名列表或单 Bot 名，而不是内部 ID', async () => {
    const mockedFetchGroupDetail = fetchGroupDetail as jest.Mock;
    mockedFetchGroupDetail.mockResolvedValue({
      group_id: 'bcs_grp_display',
      name: '展示协作群',
      status: 'active',
      participants: [
        { actor_id: 'bot-a:user-1', actor_kind: 'bot', name: '成员甲', role: 'driver', mode: 'auto' },
        { actor_id: 'bot-b:user-1', actor_kind: 'bot', name: '成员乙', role: 'worker', mode: 'auto' },
      ],
    });
    const groupTask = JSON.parse(JSON.stringify(baseTask)) as TaskView;
    const groupNode = groupTask.nodes[1];
    groupNode.runMode = 'coop_group';
    groupNode.groupId = 'bcs_grp_display';
    groupNode.assignee = 'bcs_grp_display';
    groupNode.artifacts = [
      {
        id: 'artifact-group',
        name: '群协作结论',
        type: 'document',
        kind: 'text',
        nodeId: groupNode.id,
        owner: 'bcs_grp_display',
        summary: '群协作结论',
        updatedAt: '2026-10-20T08:58:20.000Z',
      },
    ];
    groupTask.artifacts = [
      {
        id: 'artifact-bot',
        name: '单 Bot 结论',
        type: 'document',
        kind: 'text',
        owner: groupTask.ownerBotId,
        summary: '单 Bot 结论',
        updatedAt: '2026-10-20T08:58:20.000Z',
      },
    ];

    const view = render(<TaskPanelV2 {...baseProps({ task: groupTask })} />);

    await waitFor(() => expect(mockedFetchGroupDetail).toHaveBeenCalledWith('bcs_grp_display', ''));
    await waitFor(() => expect(screen.getAllByText('成员甲、成员乙').length).toBeGreaterThan(0));
    expect(screen.queryByText('bcs_grp_display')).toBeNull();
    expect(screen.getAllByText(/成员甲、成员乙/).length).toBe(2);

    const botArtifactRow = screen.getAllByText('单 Bot 结论')[0].closest('li');
    expect(botArtifactRow?.textContent).toContain('规划师');
    expect(botArtifactRow?.textContent).not.toContain('bot-1');
    view.unmount();
  });
});

describe('useLocalTaskPanelNav(nav 未注入的本地降级)', () => {
  beforeEach(() => {
    // jsdom 无 matchMedia 时 styled-components 不受影响,这里渲染纯壳即用
  });

  it('本地 nav 可下钻/返回,selectNode 记录选中', () => {
    const { result } = renderHook(() => useLocalTaskPanelNav());
    const target: ResolvedSessionTarget | null = { sessionId: 's-1', kind: 'bot' };

    act(() => result.current.selectRootNode('n-root'));
    expect(result.current.selectedNodeId).toBe('n-root');

    act(() => result.current.selectNode('n1', target));
    act(() => result.current.drillInto('n1', target));
    expect(result.current.selectedNodeId).toBe('n1');
    expect(result.current.drillStack).toEqual(['n1']);

    act(() => result.current.backToOverview());
    expect(result.current.drillStack).toEqual([]);
    expect(result.current.selectedNodeId).toBe('n1'); // 返回不清选中(demo 语义)
  });
});
