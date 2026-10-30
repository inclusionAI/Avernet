/** @jest-environment jsdom */
// NodeListViewV2 的节点状态、执行者、选中与子任务入口分支。
import { NodeListViewV2 } from '@/assets/TaskPanel/v2/NodeListViewV2';
import type { TaskPanelNav } from '@/assets/TaskPanel/v2/taskPanelNav';
import type { TaskNodeView, TaskView } from '@/assets/TaskPanel/types';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { describe, expect, it, jest } from '@jest/globals';
import { fireEvent, render, screen } from '@testing-library/react';

const makeNav = (): TaskPanelNav => ({
  selectedNodeId: 'n-2',
  drillStack: [],
  selectRootNode: jest.fn(),
  selectNode: jest.fn(),
  selectDefaultNode: jest.fn(),
  drillInto: jest.fn(),
  backToOverview: jest.fn(),
});

const makeNode = (id: string, overrides: Partial<TaskNodeView> = {}): TaskNodeView =>
  ({
    artifacts: [],
    hasSubTask: false,
    id,
    name: `节点-${id}`,
    sequence: 1,
    status: 'running',
    stepTraces: [],
    ...overrides,
  }) as TaskNodeView;

const nodes = [
  makeNode('n-root'),
  makeNode('n-label', { executorLabel: '成员甲' }),
  makeNode('n-assignee', { assigneeName: '成员乙' }),
  makeNode('n-group', { runMode: 'coop_group', groupName: '协作群' }),
  makeNode('n-executor', { executor: '规划师 Bot' }),
  makeNode('n-fallback-present', { status: 'done' as const }),
  makeNode('n-none', { status: 'hung', present: 'blocked' as never }),
  makeNode('n-subtask', {
    status: 'running',
    hasSubTask: true,
    subTaskId: 'sub-task',
  }),
  makeNode('n-2', {
    status: 'running',
    needsHitl: true,
    attemptCount: 2,
  }),
];

const task = {
  acceptances: ['验收标准'],
  artifacts: [],
  createdAt: '2026-10-20T10:00:00.000Z',
  dagEdges: [],
  dagNodes: [],
  description: '任务描述',
  finishedAt: null,
  goal: '任务目标',
  id: 'task-1',
  loopRound: 1,
  name: '任务',
  needsAttention: false,
  nodes,
  objective: '任务目标',
  ownerBotId: 'bot-1',
  ownerBotName: 'Bot',
  ownerUserId: 'user-1',
  progress: {
    total: nodes.length,
    pending: 0,
    planning: 0,
    running: nodes.length,
    done: 0,
    failed: 0,
    hung: 0,
    skipped: 0,
    percent: 0,
  },
  sourceLabel: 'Bot',
  status: 'EXECUTING' as const,
  taskType: 'dynamic',
  taskTypeLabel: '动态',
} as unknown as TaskView;

describe('NodeListViewV2', () => {
  it('未提供节点时渲染空态', () => {
    const emptyTask = { ...task, nodes: [] } as TaskView;
    render(<NodeListViewV2 task={emptyTask} nav={makeNav()} />);
    expect(screen.getByText('暂无任务节点')).toBeTruthy();
  });

  it('使用不同顺序的执行者可读信息', () => {
    render(<NodeListViewV2 task={task} nav={makeNav()} />);
    expect(screen.getByText('成员甲')).toBeInTheDocument();
    expect(screen.getByText('成员乙')).toBeInTheDocument();
    expect(screen.getByText('协作群')).toBeInTheDocument();
    expect(screen.getByText('规划师 Bot')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('节点点击与键盘选择分别触发 root/普通节点联动', () => {
    const nav = makeNav();
    render(<NodeListViewV2 task={task} nav={nav} onOpenSubTask={jest.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '选中节点 节点-n-root' }));
    expect(nav.selectRootNode).toHaveBeenCalledWith('n-root');

    fireEvent.click(screen.getByRole('button', { name: '选中节点 节点-n-subtask' }));
    expect(nav.selectNode).toHaveBeenCalledWith('n-subtask', null);

    fireEvent.keyDown(screen.getByRole('button', { name: '选中节点 节点-n-2' }), {
      key: 'Enter',
    });
    fireEvent.keyDown(screen.getByRole('button', { name: '选中节点 节点-n-2' }), {
      key: ' ',
    });
    fireEvent.keyDown(screen.getByRole('button', { name: '选中节点 节点-n-2' }), {
      key: 'Escape',
    });

    expect(nav.selectNode).toHaveBeenCalledTimes(3);
  });

  it('子任务入口仅开打子任务，不会触发节点选择', () => {
    const nav = makeNav();
    const onOpenSubTask = jest.fn();
    render(<NodeListViewV2 task={task} nav={nav} onOpenSubTask={onOpenSubTask} />);

    fireEvent.click(screen.getByTitle('查看子任务'));
    expect(onOpenSubTask).toHaveBeenCalledWith('sub-task');
    expect(nav.selectRootNode).not.toHaveBeenCalled();
    expect(nav.selectNode).not.toHaveBeenCalled();
  });

  it('hasSubTask 但未传入 onOpenSubTask 时保持原样式', () => {
    render(<NodeListViewV2 task={task} nav={makeNav()} />);
    expect(screen.queryByTitle('查看子任务')).toBeNull();
  });
});

describe('NodeListViewV2 返工兜底分支', () => {
  it('attemptCount 缺失时不渲染返工徽章', () => {
    const noAttemptTask = {
      ...task,
      nodes: [makeNode('n-no-attempt')],
    } as TaskView;
    render(<NodeListViewV2 task={noAttemptTask} nav={makeNav()} />);

    expect(screen.getByRole('button', { name: '选中节点 节点-n-no-attempt' })).toBeInTheDocument();
    expect(screen.queryByText(/次返工/)).toBeNull();
  });
});
