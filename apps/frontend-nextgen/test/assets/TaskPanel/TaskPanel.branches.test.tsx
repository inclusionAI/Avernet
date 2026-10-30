/** @jest-environment jsdom */
// TaskPanel 自身渲染分支：taskInfoFallback 兜底链、loading、error+retry、空态。
// 不直接测 Fetcher/V2 内部，只固定其输出以隔离 TaskPanel 的 props 组装与 JSX 分支。
import { TaskPanel } from '@/assets/TaskPanel/TaskPanel';
import type { TaskView } from '@/assets/TaskPanel/types';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

type FetcherState = {
  task: TaskView | null;
  loading: boolean;
  error: string | null;
  retry: () => void;
};

// 固定 fetcher 输出，隔离 TaskPanel 子节点 JSX 分支(工厂只引用 global,避免 out-of-scope 限制)
jest.mock('@/assets/TaskPanel/TaskPanelFetcher', () => ({
  TaskPanelFetcher: ({
    children,
  }: {
    children: (state: FetcherState) => React.ReactNode;
  }) => children((global as { __TPF_STATE?: FetcherState }).__TPF_STATE as FetcherState),
}));

// 固定 V2，仅捕获注入的 resolvedTask 以断言兜底链
jest.mock('@/assets/TaskPanel/v2/TaskPanelV2', () => ({
  TaskPanelV2: ({ task }: { task: TaskView }) => (
    <div data-testid="v2">
      {task.taskTypeLabel}|{task.sourceLabel}|{task.ownerBotName}|{task.createdAt}|{task.finishedAt === null ? 'null' : task.finishedAt}
    </div>
  ),
}));

const mockRetry = jest.fn();

const emptyLabelsTask = {
  id: 't',
  name: 'n',
  description: '',
  goal: '',
  objective: '',
  acceptances: [],
  status: 'EXECUTING',
  taskType: 'dynamic',
  taskTypeLabel: '',
  sourceLabel: '',
  ownerBotName: '',
  ownerBotId: '',
  ownerUserId: 'u',
  createdAt: '',
  finishedAt: null,
  loopRound: 0,
  needsAttention: false,
  progress: { total: 0, pending: 0, planning: 0, running: 0, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
  artifacts: [],
  nodes: [],
  dagNodes: [],
  dagEdges: [],
} as unknown as TaskView;

const fullLabelsTask = {
  ...emptyLabelsTask,
  taskTypeLabel: '动态',
  sourceLabel: 'Bot',
  ownerBotName: '规划师',
  createdAt: '2026-10-30 10:00',
  finishedAt: '2026-10-30 11:00',
} as TaskView;

const setState = (s: FetcherState) => {
  (global as { __TPF_STATE?: FetcherState }).__TPF_STATE = s;
};

beforeEach(() => {
  jest.clearAllMocks();
  setState({ task: null, loading: false, error: null, retry: mockRetry });
});
afterEach(() => {
  delete (global as { __TPF_STATE?: FetcherState }).__TPF_STATE;
});

describe('TaskPanel 渲染分支', () => {
  it('task 字段齐全 → 直用任务值(命中每个 || 的第一臂)', () => {
    setState({ task: fullLabelsTask, loading: false, error: null, retry: mockRetry });
    render(<TaskPanel apiBaseUrl="" taskId="t" />);
    expect(screen.getByTestId('v2').textContent).toBe('动态|Bot|规划师|2026-10-30 10:00|2026-10-30 11:00');
  });

  it('task 字段空 + taskInfoFallback 提供 → 命中第二臂回退到 fallback', () => {
    setState({ task: emptyLabelsTask, loading: false, error: null, retry: mockRetry });
    render(
      <TaskPanel
        apiBaseUrl=""
        taskId="t"
        taskInfoFallback={{
          taskTypeLabel: '兜底类型',
          sourceLabel: '兜底来源',
          ownerBotName: '兜底Bot',
          createdAt: '2026-10-29 09:00',
          finishedAt: '2026-10-29 10:00',
        }}
      />,
    );
    expect(screen.getByTestId('v2').textContent).toBe('兜底类型|兜底来源|兜底Bot|2026-10-29 09:00|2026-10-29 10:00');
  });

  it('task 字段空且无 fallback → 命中第三臂默认值(—/—/—/空/null)', () => {
    setState({ task: emptyLabelsTask, loading: false, error: null, retry: mockRetry });
    render(<TaskPanel apiBaseUrl="" taskId="t" />);
    expect(screen.getByTestId('v2').textContent).toBe('—|—|—||null');
  });

  it('loading 且无 task → 渲染加载中', () => {
    setState({ task: null, loading: true, error: null, retry: mockRetry });
    render(<TaskPanel apiBaseUrl="" taskId="t" />);
    expect(screen.getByText('加载任务中…')).toBeInTheDocument();
  });

  it('error 且无 task → 渲染失败+重试,点击触发 retry', () => {
    setState({ task: null, loading: false, error: 'boom', retry: mockRetry });
    render(<TaskPanel apiBaseUrl="" taskId="t" />);
    expect(screen.getByText(/加载失败/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('重试'));
    expect(mockRetry).toHaveBeenCalledTimes(1);
  });

  it('无 task/loading/error → 渲染空容器(无 V2/无状态)', () => {
    setState({ task: null, loading: false, error: null, retry: mockRetry });
    const { container } = render(<TaskPanel apiBaseUrl="" taskId="t" />);
    expect(container.querySelector('[data-task-panel-root="true"]')).toBeInTheDocument();
    expect(screen.queryByTestId('v2')).not.toBeInTheDocument();
    expect(screen.queryByText('加载任务中…')).not.toBeInTheDocument();
  });
});
