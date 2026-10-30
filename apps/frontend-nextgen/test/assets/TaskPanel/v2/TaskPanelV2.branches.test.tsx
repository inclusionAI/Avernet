/** @jest-environment jsdom */
// TaskPanelV2 的默认节点、面包屑短名、状态兜底与收起交互分支。
import type { TaskPanelNav } from '@/assets/TaskPanel/v2/taskPanelNav';
import { TaskPanelV2 } from '@/assets/TaskPanel/v2/TaskPanelV2';
import type { TaskView } from '@/assets/TaskPanel/types';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, jest } from '@jest/globals';

const makeTask = (status: TaskView['status'] = 'EXECUTING', nodes: TaskView['nodes'] = []): TaskView =>
  ({
    id: 'task-branches',
    name: '分析任务',
    description: '',
    goal: '',
    objective: '',
    acceptances: [],
    status,
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
    progress: { total: 1, pending: 0, planning: 0, running: 1, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
    artifacts: [],
    nodes,
    dagNodes: [],
    dagEdges: [],
  }) as TaskView;

const makeNav = (): TaskPanelNav & { calls: { default: string[]; back: number[] } } => {
  const calls = { default: [] as string[], back: [] as number[] };
  return {
    selectedNodeId: null,
    drillStack: [],
    selectRootNode: jest.fn(),
    selectNode: jest.fn(),
    selectDefaultNode: (id: string) => calls.default.push(id),
    drillInto: jest.fn(),
    backToOverview: () => calls.back.push(1),
    calls,
  } as unknown as TaskPanelNav & { calls: typeof calls };
};

const renderPanel = (task: TaskView, nav: TaskPanelNav = makeNav(), overrides = {}) =>
  render(<TaskPanelV2 task={task} nav={nav} apiBaseUrl="" {...overrides} />);

describe('TaskPanelV2 壳分支', () => {
  it('没有节点时不会写入默认选中', () => {
    const nav = makeNav();
    renderPanel(makeTask(), nav);
    expect(nav.calls.default).toEqual([]);
  });

  it('已选中节点不会重写入默认节点', () => {
    const nav = makeNav();
    nav.selectedNodeId = 'n-first';
    renderPanel(
      makeTask('EXECUTING', [
        {
          id: 'n-first',
          name: '第一节点',
          sequence: 1,
          status: 'running',
          hasSubTask: false,
          artifacts: [],
          stepTraces: [],
        },
      ]),
      nav,
    );
    expect(nav.calls.default).toEqual([]);
  });

  it('L2 节点缺少 dagNodes short 时使用节点名截断', () => {
    const nav = makeNav();
    nav.drillStack = ['n-long'];
    renderPanel(
      makeTask('EXECUTING', [
        {
          id: 'n-long',
          name: '一个非常长的第二个节点标题',
          sequence: 1,
          status: 'running',
          hasSubTask: false,
          artifacts: [],
          stepTraces: [],
        },
      ]),
      nav,
    );
    expect(screen.getByText('一个非常长的第…')).toBeInTheDocument();
  });

  it('未知任务状态直接展示原始状态', () => {
    renderPanel(makeTask('UNKNOWN_STATUS' as never));
    expect(screen.getByText('UNKNOWN_STATUS')).toBeInTheDocument();
  });

  it('收起按钮 hover 状态可与透明背景互切', () => {
    const onTogglePanel = jest.fn();
    const { unmount } = renderPanel(makeTask(), makeNav(), {
      onTogglePanel,
      className: 'custom-panel',
      style: { minHeight: 240 },
    });
    const button = screen.getByRole('button', { name: '收起任务副屏' });

    fireEvent.mouseEnter(button);
    expect(button.style.background).not.toBe('transparent');
    fireEvent.mouseLeave(button);
    expect(button.style.background).toBe('transparent');
    fireEvent.click(button);
    expect(onTogglePanel).toHaveBeenCalledTimes(1);

    unmount();
  });
});
