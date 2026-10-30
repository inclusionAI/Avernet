/** @jest-environment jsdom */
// TaskOverviewV2 的 DAG 节点点击分支。
import { TaskOverviewV2 } from '@/assets/TaskPanel/v2/TaskOverviewV2';
import type { TaskPanelNav } from '@/assets/TaskPanel/v2/taskPanelNav';
import type { TaskView } from '@/assets/TaskPanel/types';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, jest } from '@jest/globals';

const node = (id: string, name: string, overrides = {}) => ({
  id,
  name,
  sequence: 1,
  status: 'running',
  hasSubTask: false,
  artifacts: [],
  stepTraces: [],
  ...overrides,
});

const task = {
  id: 'task-overview',
  name: 'Task',
  description: '',
  goal: '',
  objective: '',
  acceptances: [],
  status: 'EXECUTING',
  taskType: 'dynamic',
  taskTypeLabel: 'Dynamic',
  sourceLabel: '',
  ownerBotName: 'Bot',
  ownerBotId: 'bot-1',
  ownerUserId: 'user-1',
  createdAt: '',
  finishedAt: null,
  loopRound: 1,
  needsAttention: false,
  progress: { total: 1, pending: 0, planning: 0, running: 1, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
  artifacts: [],
  nodes: [
    node('root-node', '节点'),
    node('group-node', '协作节点', {
      sequence: 2,
      runMode: 'coop_group',
      sessionId: 'session-group',
      groupId: 'group-1',
    }),
  ],
  dagNodes: [
    { id: 'root-node', label: '节点', status: 'running', x: 0, y: 0, isCurrent: false },
    { id: 'group-node', label: '协作节点', status: 'running', x: 0, y: 1, isCurrent: false },
    { id: 'missing-node', label: '失踪节点', status: 'running', x: 0, y: 2, isCurrent: false },
  ],
  dagEdges: [],
} as unknown as TaskView;

const makeNav = (): TaskPanelNav & {
  calls: { root: string[]; select: string[][] };
} => {
  const calls = { root: [] as string[], select: [] as string[][] };
  return {
    selectedNodeId: null,
    drillStack: [],
    selectRootNode: (id: string) => calls.root.push(id),
    selectNode: (id: string) => calls.select.push([id]),
    selectDefaultNode: jest.fn(),
    drillInto: jest.fn(),
    backToOverview: jest.fn(),
    calls,
  } as unknown as TaskPanelNav & { calls: typeof calls };
};

describe('TaskOverviewV2 DAG 点击', () => {
  it('根节点、普通节点、缺失节点分别触发对应导航语义', () => {
    const nav = makeNav();
    render(<TaskOverviewV2 task={task} nav={nav} apiBaseUrl="" />);
    fireEvent.click(screen.getByTitle('切换到 DAG 视图'));

    fireEvent.keyDown(screen.getByLabelText('查看节点 节点'), { key: 'Enter' });
    fireEvent.keyDown(screen.getByLabelText('查看节点 协作节点'), { key: 'Enter' });
    fireEvent.keyDown(screen.getByLabelText('查看节点 失踪节点'), { key: 'Enter' });

    expect(nav.calls.root).toEqual(['root-node']);
    expect(nav.calls.select).toHaveLength(1);
    expect(nav.calls.select[0][0]).toBe('group-node');
  });
});
