/** @jest-environment jsdom */
import {
  PublicTaskCatalogPanel,
  type TaskCatalogViewModel,
} from '@/components/CollaborationSquare/PublicTaskCatalogPanel';
import type { PublicTask } from '@/domain/collaborationSquare/types';
import '@testing-library/jest-dom';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import path from 'node:path';

jest.mock('@/components/CollaborationSquare/TaskCard', () => ({
  __esModule: true,
  default: ({ task, onOpenDetail }: { task: PublicTask; onOpenDetail: (task: PublicTask) => void }) => (
    <div data-testid="task-card" data-task-id={task.id}>
      {task.name}
      <button type="button" data-testid={`open-${task.id}`} onClick={() => onOpenDetail(task)}>
        详情
      </button>
    </div>
  ),
  TaskStatusBadge: ({ status }: { status: string }) => <span data-testid="task-status-badge">{status}</span>,
  formatTaskDate: (iso: string) => iso.slice(0, 10),
  TaskAvatar: () => <span data-testid="task-avatar" aria-hidden />,
}));

const sampleTasks: PublicTask[] = [
  {
    id: 't1',
    name: '梳理路线图',
    goal: '对齐 Q3 方向',
    acceptanceCriteria: ['覆盖核心方向'],
    status: 'pending_claim',
    publisherBotName: '产品协作助手',
    publishedAt: '2026-08-19T09:00:00Z',
  },
  {
    id: 't2',
    name: '竞品研究',
    goal: '完成竞品对比',
    acceptanceCriteria: ['输出对比矩阵'],
    status: 'completed',
    publisherBotName: '研究分析助手',
    publishedAt: '2026-08-20T10:30:00Z',
  },
];

function buildVm(overrides: Partial<TaskCatalogViewModel> = {}): TaskCatalogViewModel {
  return {
    tasks: [],
    taskQuery: '',
    taskStatusFilter: 'all',
    setTaskQuery: jest.fn(),
    setTaskStatusFilter: jest.fn(),
    resetTaskFilters: jest.fn(),
    loading: false,
    error: null,
    hasMore: false,
    loadingMore: false,
    loadMore: jest.fn(),
    loadMoreError: null,
    reload: jest.fn(),
    openTaskDetail: jest.fn(),
    selectedTaskId: null,
    taskDetail: null,
    detailLoading: false,
    closeTaskDetail: jest.fn(),
    ...overrides,
  };
}

describe('PublicTaskCatalogPanel', () => {
  test('加载态渲染骨架占位网格', () => {
    render(<PublicTaskCatalogPanel vm={buildVm({ loading: true })} />);
    expect(screen.getByLabelText('正在加载任务广场')).toBeInTheDocument();
  });

  test('错误态渲染失败说明与重新加载按钮，点击触发 reload', async () => {
    const user = userEvent.setup();
    const vm = buildVm({ error: '整页失效' });
    render(<PublicTaskCatalogPanel vm={vm} />);
    expect(screen.getByText('任务广场加载失败')).toBeInTheDocument();
    expect(screen.getByText('整页失效')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重新加载' }));
    expect(vm.reload).toHaveBeenCalledTimes(1);
  });

  test('默认空态展示当前暂无公开任务且不显示清除筛选', () => {
    render(<PublicTaskCatalogPanel vm={buildVm()} />);
    expect(screen.getByText('当前暂无公开任务')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /清除筛选/ })).not.toBeInTheDocument();
  });

  test('筛选无结果空态展示未找到文案（含当前状态文案）与清除筛选按钮', async () => {
    const user = userEvent.setup();
    const vm = buildVm({ taskStatusFilter: 'claimed', taskQuery: '路线' });
    render(<PublicTaskCatalogPanel vm={vm} />);
    expect(screen.getByText('未找到符合条件的任务')).toBeInTheDocument();
    // 描述带当前状态文案（与状态分段按钮文案区分，匹配带前缀的描述串）
    expect(screen.getByText(/状态：已认领/)).toBeInTheDocument();
    const clear = screen.getByRole('button', { name: '清除筛选' });
    await user.click(clear);
    expect(vm.resetTaskFilters).toHaveBeenCalledTimes(1);
  });

  test('正常网格渲染每个任务卡，无筛选时不显示结果摘要', () => {
    render(<PublicTaskCatalogPanel vm={buildVm({ tasks: sampleTasks })} />);
    expect(screen.getAllByTestId('task-card')).toHaveLength(2);
    expect(screen.queryByText(/命中/)).not.toBeInTheDocument();
  });

  test('网格存在且正在加载下一页时展示「正在加载更多」提示', () => {
    render(<PublicTaskCatalogPanel vm={buildVm({ tasks: sampleTasks, hasMore: true, loadingMore: true })} />);
    expect(screen.getByText('正在加载更多...')).toBeInTheDocument();
  });

  test('加载更多失败时展示失败说明与重试按钮，点击触发 loadMore', async () => {
    const user = userEvent.setup();
    const vm = buildVm({ tasks: sampleTasks, loadMoreError: '加载更多失败，请稍后重试' });
    render(<PublicTaskCatalogPanel vm={vm} />);
    expect(screen.getByText('加载更多失败，请稍后重试')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(vm.loadMore).toHaveBeenCalledTimes(1);
  });

  test('有筛选且有结果时显示命中数量与清除筛选', async () => {
    const user = userEvent.setup();
    const vm = buildVm({ tasks: sampleTasks, taskQuery: '路线' });
    render(<PublicTaskCatalogPanel vm={vm} />);
    expect(screen.getByText(/命中 2 个任务/)).toBeInTheDocument();
    const clear = screen.getByRole('button', { name: '清除筛选' });
    await user.click(clear);
    expect(vm.resetTaskFilters).toHaveBeenCalledTimes(1);
  });

  test('搜索框输入回调和清除按钮回调触发 setTaskQuery', async () => {
    const user = userEvent.setup();
    const vm = buildVm({ tasks: sampleTasks, taskQuery: '既有' });
    render(<PublicTaskCatalogPanel vm={vm} />);
    const input = screen.getByLabelText('搜索任务') as HTMLInputElement;
    expect(input.value).toBe('既有');
    await user.clear(input);
    await user.type(input, '路线图');
    expect(vm.setTaskQuery).toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '清除搜索' }));
    expect(vm.setTaskQuery).toHaveBeenCalledWith('');
  });

  test('状态分段切换调用 setTaskStatusFilter', async () => {
    const user = userEvent.setup();
    const vm = buildVm({ tasks: sampleTasks });
    render(<PublicTaskCatalogPanel vm={vm} />);
    // Segmented 渲染为按钮组，点击「已认领」
    await user.click(screen.getByRole('button', { name: '已认领' }));
    expect(vm.setTaskStatusFilter).toHaveBeenCalledWith('claimed');
  });

  test('状态分段包含「待验收」选项，点击触发 setTaskStatusFilter("reviewing")', async () => {
    const user = userEvent.setup();
    const vm = buildVm({ tasks: sampleTasks });
    render(<PublicTaskCatalogPanel vm={vm} />);
    await user.click(screen.getByRole('button', { name: '待验收' }));
    expect(vm.setTaskStatusFilter).toHaveBeenCalledWith('reviewing');
  });

  test('状态分段具有可访问名称（aria-label 任务状态筛选）', () => {
    render(<PublicTaskCatalogPanel vm={buildVm({ tasks: sampleTasks })} />);
    expect(screen.getByRole('group', { name: '任务状态筛选' })).toBeInTheDocument();
  });

  test('任务卡的查看详情入口通过 openTaskDetail 回调上抛', async () => {
    const user = userEvent.setup();
    const vm = buildVm({ tasks: [sampleTasks[0]] });
    render(<PublicTaskCatalogPanel vm={vm} />);
    await user.click(screen.getByTestId('open-t1'));
    expect(vm.openTaskDetail).toHaveBeenCalledWith(sampleTasks[0]);
  });

  test('面板挂载只读详情弹层：selectedTaskId 驱动 open，展示 taskDetail 完整字段', () => {
    const vm = buildVm({ tasks: sampleTasks, selectedTaskId: 't1', taskDetail: sampleTasks[0] });
    render(<PublicTaskCatalogPanel vm={vm} />);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    // 完整目标（卡片 mock 不渲染 goal，故 goal 文案只来自弹层）
    expect(within(dialog).getByText('对齐 Q3 方向')).toBeInTheDocument();
    // 全部验收标准
    expect(within(dialog).getByText('覆盖核心方向')).toBeInTheDocument();
  });

  test('未选中任务时弹层不渲染（open 受 selectedTaskId 控制）', () => {
    render(<PublicTaskCatalogPanel vm={buildVm({ tasks: sampleTasks })} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  test('弹层加载态由 detailLoading 驱动：弹层开但 taskDetail 为空时显示骨架而不崩', () => {
    const vm = buildVm({ tasks: sampleTasks, selectedTaskId: 't1', taskDetail: null, detailLoading: true });
    render(<PublicTaskCatalogPanel vm={vm} />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText('正在加载任务详情')).toBeInTheDocument();
  });

  test('弹层关闭途径调用 closeTaskDetail', async () => {
    const user = userEvent.setup();
    const vm = buildVm({ tasks: sampleTasks, selectedTaskId: 't1', taskDetail: sampleTasks[0] });
    render(<PublicTaskCatalogPanel vm={vm} />);
    await user.click(screen.getByRole('button', { name: '关闭任务详情' }));
    expect(vm.closeTaskDetail).toHaveBeenCalledTimes(1);
  });

  test('弹出弹层为只读：仅一个关闭按钮，无写操作按钮', () => {
    const vm = buildVm({ tasks: sampleTasks, selectedTaskId: 't1', taskDetail: sampleTasks[0] });
    render(<PublicTaskCatalogPanel vm={vm} />);
    const dialog = screen.getByRole('dialog');
    const dialogButtons = within(dialog).getAllByRole('button');
    expect(dialogButtons).toHaveLength(1);
    expect(dialogButtons[0]).toHaveAccessibleName('关闭任务详情');
    const writeActions = ['认领', '提交', '取消', '对话', '跳转', '验收', '重新认领'];
    writeActions.forEach((keyword) => {
      expect(within(dialog).queryByRole('button', { name: new RegExp(keyword) })).not.toBeInTheDocument();
    });
  });

  test('面板源码遵守 UI 与分层约束（无裸 button/dialog/select、animate-pulse、bg-gray、internal）且挂载弹层', () => {
    const source = readFileSync(
      path.join(process.cwd(), 'src/components/CollaborationSquare/PublicTaskCatalogPanel/index.tsx'),
      'utf8',
    );
    expect(source).not.toContain('<button');
    expect(source).not.toContain('<dialog');
    expect(source).not.toContain('<select');
    expect(source).not.toContain('animate-pulse');
    expect(source).not.toContain('bg-gray-');
    expect(source).not.toContain('message.');
    expect(source).not.toContain('src/internal');
    expect(source).toContain('TaskDetailModal');
  });
});
