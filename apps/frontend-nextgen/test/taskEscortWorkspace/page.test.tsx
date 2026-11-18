/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import TaskEscortPage from '@/pages/TaskEscort';
import { Dashboard } from '@/pages/TaskEscort/Dashboard';
import { taskEscortService } from '@/services/taskEscort/taskEscortService';
import { readTaskEscortWorkflows } from '@/services/taskEscortParity/workspaceService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { history } from '@umijs/max';

const mockScope = {
  key: 'user-a:space-a',
  userId: 'user-a',
  ready: true,
  loading: false,
  error: undefined as string | undefined,
  retry: jest.fn(),
};
const mockLeafReads = jest.fn();
function mockLeaf(name: string) {
  return ({ context }: { context: TaskEscortParityContext }) => {
    mockLeafReads(name, context);
    return (
      <div data-testid="parity-leaf">
        {name}:{context.workflowId}:{context.query.runId}
      </div>
    );
  };
}
jest.mock('../../src/pages/TaskEscort/parity/overview/OverviewPage', () => ({ OverviewPage: mockLeaf('runs') }), {
  virtual: true,
});
jest.mock('../../src/pages/TaskEscort/parity/evolution/IssuePage', () => ({ IssuePage: mockLeaf('issues') }), {
  virtual: true,
});
jest.mock(
  '../../src/pages/TaskEscort/parity/evolution/ExperiencePage',
  () => ({ ExperiencePage: mockLeaf('experiences') }),
  { virtual: true },
);
jest.mock(
  '../../src/pages/TaskEscort/parity/editor/WorkflowEditorPage',
  () => ({ WorkflowEditorPage: mockLeaf('editor') }),
  { virtual: true },
);
jest.mock(
  '../../src/pages/TaskEscort/parity/management/ManagementPage',
  () => ({ ManagementPage: mockLeaf('settings') }),
  { virtual: true },
);
jest.mock('@/pages/TaskEscort/Dashboard', () => ({ Dashboard: jest.fn(() => <h1>数据大盘</h1>) }));
jest.mock('@umijs/max', () => {
  const router = require('history').createMemoryHistory();
  const React = require('react');
  return {
    history: router,
    useLocation: () =>
      React.useSyncExternalStore(
        (notify: () => void) => router.listen(notify),
        () => router.location,
      ),
  };
});
jest.mock('@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceScope', () => ({
  useTaskEscortWorkspaceScope: () => mockScope,
}));
jest.mock('sonner', () => ({ toast: Object.assign(jest.fn(), { success: jest.fn(), error: jest.fn() }) }));
jest.mock('@/services/taskEscort/taskEscortService', () => ({
  taskEscortService: {
    listWorkflows: jest.fn(),
    listFlowRuns: jest.fn(),
    getFlowRun: jest.fn(),
  },
}));
jest.mock('@/services/taskEscortParity/workspaceService', () => ({ readTaskEscortWorkflows: jest.fn() }));
const read = jest.mocked(readTaskEscortWorkflows);
const rows = [{ id: 'real-a', title: '真实工作流 A', runCount: 0, lastStatus: 'succeeded', ownerId: 'owner-a' }];
const items = [
  ['dashboard', '数据大盘'],
  ['runs', '运行概览'],
  ['issues', '问题与优化'],
  ['experiences', '可复用经验'],
  ['editor', '编辑器'],
  ['settings', '管理设置'],
];

beforeEach(() => {
  jest.clearAllMocks();
  Object.assign(mockScope, { key: 'user-a:space-a', ready: true, loading: false, error: undefined });
  read.mockResolvedValue(rows);
  history.replace('/evolution-factory/task-escort');
});

it.each(items.flatMap(([id, label]) => [false, true].map((legacy) => ({ id, label, legacy }))))(
  '$id legacy=$legacy renders exactly six real navigation destinations without sample preload/mode UI',
  async ({ id, label, legacy }) => {
    history.replace(`/evolution-factory/task-escort?view=${id}${legacy ? '&demo=1' : ''}`);
    const { container } = render(<TaskEscortPage />);
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    const nav = screen.getByRole('navigation', { name: '任务护航导航' });
    expect(within(nav).getAllByRole('button')).toHaveLength(6);
    expect(within(nav).getByRole('button', { name: label })).toHaveAttribute('aria-current', 'page');
    expect(within(nav).queryByRole('button', { name: '工作台' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: label })).toBeInTheDocument();
    expect(taskEscortService.listWorkflows).not.toHaveBeenCalled();
    expect(taskEscortService.listFlowRuns).not.toHaveBeenCalled();
    const root = container.querySelector('.task-escort-workspace-root')!;
    expect(root.children).toHaveLength(1);
    expect(root.textContent).not.toMatch(/演示|合成|研发效能|真实只读|护航工作台/);
    expect(screen.queryByRole('tab', { name: '进化任务' })).not.toBeInTheDocument();
    expect(container.querySelector('.guard-sidebar')).toHaveClass(
      id === 'editor' ? 'guard-sidebar-compact' : 'guard-sidebar',
    );
  },
);

it.each(items.slice(1))('%s receives a real workflow context and truthful metadata', async (id) => {
  history.replace(`/evolution-factory/task-escort?view=${id}`);
  render(<TaskEscortPage />);
  expect(await screen.findByTestId('parity-leaf')).toHaveTextContent(`${id}:real-a:`);
  expect(screen.getByText('0 次运行')).toBeInTheDocument();
  expect(screen.getByText('owner-a')).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: /切换工作流/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '新建工作流' })).toBeDisabled();
  expect(screen.getByText(/新建工作流尚未接入/)).toBeInTheDocument();
});

it('missing metadata stays unknown instead of becoming fabricated totals, owners or statuses', async () => {
  read.mockResolvedValue([{ ...rows[0], runCount: null, ownerId: null, lastStatus: null }]);
  history.replace('/evolution-factory/task-escort?view=runs');
  render(<TaskEscortPage />);
  expect(await screen.findByText('运行次数未知')).toBeInTheDocument();
  expect(screen.getByText('最近状态未知')).toBeInTheDocument();
  expect(screen.queryByText('0 次运行')).not.toBeInTheDocument();
  expect(screen.queryByText(/负责人/)).not.toBeInTheDocument();
});

it('reference aliases and old workbench deep links reach the new typed leaves and preserve actual focus', async () => {
  history.replace('/evolution-factory/task-escort?view=workbench&workflowId=real-a&runId=actual-run');
  render(<TaskEscortPage />);
  expect(await screen.findByTestId('parity-leaf')).toHaveTextContent('issues:real-a:actual-run');
  fireEvent.click(screen.getByRole('button', { name: '可复用经验' }));
  expect(await screen.findByTestId('parity-leaf')).toHaveTextContent('experiences:real-a:actual-run');
});

it('navigation removes old demo but preserves host query/hash, and does not refetch generic samples', async () => {
  history.replace('/evolution-factory/task-escort?demo=1&host=yes#detail');
  render(<TaskEscortPage />);
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  for (const [id, label] of items.slice(1)) {
    fireEvent.click(screen.getByRole('button', { name: label }));
    expect(new URLSearchParams(history.location.search).get('view')).toBe(id);
    expect(history.location.search).not.toContain('demo=');
    expect(history.location.search).toContain('host=yes');
    expect(history.location.hash).toBe('#detail');
    await screen.findByTestId('parity-leaf');
  }
  expect(read).toHaveBeenCalledTimes(1);
  expect(taskEscortService.listFlowRuns).not.toHaveBeenCalled();
});

it('dashboard keeps the actual legacy controller/scope, with sequential context navigation + workflow selection', async () => {
  read.mockResolvedValue([...rows, { ...rows[0], id: 'real-b', title: 'B' }]);
  render(<TaskEscortPage />);
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  await act(async () => undefined);
  const props = jest.mocked(Dashboard).mock.calls.at(-1)![0] as unknown as {
    demo: { navigate: (page: 'workbench') => void; setWorkflowId: (id: string) => void };
    scope: unknown;
  };
  expect(props.scope).toBe(mockScope);
  act(() => {
    props.demo.navigate('workbench');
    props.demo.setWorkflowId('real-b');
  });
  expect(await screen.findByTestId('parity-leaf')).toHaveTextContent('issues:real-b:');
});

it.each(['403 forbidden', '工作流列表格式无效'])(
  'metadata errors block leaves and retry truthfully: %s',
  async (message) => {
    history.replace('/evolution-factory/task-escort?view=runs');
    read.mockRejectedValueOnce(new Error(message));
    render(<TaskEscortPage />);
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByTestId('parity-leaf')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试读取' }));
    expect(await screen.findByTestId('parity-leaf')).toBeInTheDocument();
  },
);

it('empty metadata blocks leaves, while global dashboard never requires a fake workflow', async () => {
  read.mockResolvedValue([]);
  history.replace('/evolution-factory/task-escort?view=runs');
  render(<TaskEscortPage />);
  expect(await screen.findByText('暂无可访问的工作流')).toBeInTheDocument();
  expect(screen.queryByTestId('parity-leaf')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '数据大盘' }));
  expect(screen.getByRole('heading', { name: '数据大盘' })).toBeInTheDocument();
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
});

it('identity loading/failure blocks reads and retries the existing scope adapter', async () => {
  history.replace('/evolution-factory/task-escort?view=editor');
  Object.assign(mockScope, { ready: false, loading: true });
  const { rerender } = render(<TaskEscortPage />);
  expect(screen.getByRole('status')).toBeInTheDocument();
  expect(read).not.toHaveBeenCalled();
  Object.assign(mockScope, { loading: false, error: '身份不可用' });
  rerender(<TaskEscortPage />);
  expect(screen.getByText('身份不可用')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重试读取' }));
  expect(mockScope.retry).toHaveBeenCalledTimes(1);
  expect(screen.queryByTestId('parity-leaf')).not.toBeInTheDocument();
});

it.each(items.slice(1))(
  '%s blocks inaccessible explicit workflow detail instead of mounting a fallback leaf',
  async (id) => {
    history.replace(
      `/evolution-factory/task-escort?view=${id}&workflowId=not-accessible&runId=old-run&analysisId=old-analysis&issueSignature=old-issue`,
    );
    render(<TaskEscortPage />);
    expect(await screen.findByText(/所选工作流不存在或当前身份无权访问/)).toBeInTheDocument();
    expect(screen.queryByTestId('parity-leaf')).not.toBeInTheDocument();
    expect(mockLeafReads).not.toHaveBeenCalled();
    expect(screen.queryByText('0 次运行')).not.toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: /切换工作流/ })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '重试读取' }));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/所选工作流不存在或当前身份无权访问/)).toBeInTheDocument();
    expect(mockLeafReads).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('combobox', { name: /切换工作流/ }));
    fireEvent.click(await screen.findByRole('button', { name: /真实工作流 A/ }));
    expect(await screen.findByTestId('parity-leaf')).toHaveTextContent(`${id}:real-a:`);
    expect(history.location.search).not.toMatch(/runId|analysisId|issueSignature/);
  },
);
