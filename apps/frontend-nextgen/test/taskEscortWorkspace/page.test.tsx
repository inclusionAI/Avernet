/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useEditorAuthoringNavigation } from '@/hooks/taskEscortParity/useEditorAuthoringNavigation';
import { useTaskEscortDashboardAccess } from '@/hooks/taskEscortWorkspace/useTaskEscortDashboardAccess';
import TaskEscortPage from '@/pages/TaskEscort';
import { Dashboard } from '@/pages/TaskEscort/Dashboard';
import { taskEscortService } from '@/services/taskEscort/taskEscortService';
import { readTaskEscortWorkflows } from '@/services/taskEscortParity/workspaceService';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
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
let mockEditorDirty = false;
function mockLeaf(name: string) {
  return function MockLeaf({ context }: { context: TaskEscortParityContext }) {
    const navigation = useEditorAuthoringNavigation(name === 'editor' && mockEditorDirty);
    mockLeafReads(name, context);
    return (
      <>
        <div data-testid="parity-leaf">
          {name}:{context.workflowId}:{context.query.runId}
        </div>
        {navigation.leave && (
          <div role="dialog" aria-label="未保存编辑">
            <button type="button" onClick={navigation.cancelLeave}>
              继续编辑
            </button>
            <button type="button" onClick={navigation.discardAndLeave}>
              放弃并离开
            </button>
          </div>
        )}
      </>
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
jest.mock('@/hooks/taskEscortWorkspace/useTaskEscortDashboardAccess', () => ({
  useTaskEscortDashboardAccess: jest.fn(() => 'allowed'),
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
jest.mock('@/services/taskEscortParity/workflowAuthoringService', () => ({
  workflowAuthoringService: { createBlank: jest.fn() },
}));
const read = jest.mocked(readTaskEscortWorkflows);
const dashboardAccess = jest.mocked(useTaskEscortDashboardAccess);
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
  mockEditorDirty = false;
  Object.assign(mockScope, { key: 'user-a:space-a', userId: 'user-a', ready: true, loading: false, error: undefined });
  dashboardAccess.mockReturnValue('allowed');
  read.mockResolvedValue(rows);
  history.replace('/evolution-factory/task-escort');
});

it.each(items.flatMap(([id, label]) => [false, true].map((legacy) => ({ id, label, legacy }))))(
  '$id legacy=$legacy renders six real destinations and product navigation without sample preload',
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
    expect(root.textContent).not.toMatch(/DEMO|无工作流|演示|合成|研发效能|真实只读|护航工作台/i);
    expect(screen.getByRole('button', { name: '产品导航' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: '工作区' })).toHaveAttribute('aria-pressed', 'true');
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
  expect(screen.getByRole('button', { name: '新建工作流' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '新建工作流' }));
  expect(screen.getByRole('dialog', { name: '新建工作流' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '空白工作流' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
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
  history.replace('/evolution-factory/task-escort?view=dashboard');
  let resolveWorkflows!: (workflows: typeof rows) => void;
  const workflows = new Promise<typeof rows>((resolve) => {
    resolveWorkflows = resolve;
  });
  read.mockReturnValue(workflows);
  render(<TaskEscortPage />);
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  // Wait for metadata, not just request dispatch, before capturing workflow-selection callbacks.
  await act(async () => {
    resolveWorkflows([...rows, { ...rows[0], id: 'real-b', title: 'B' }]);
    await workflows;
  });
  const props = jest.mocked(Dashboard).mock.calls.at(-1)![0] as unknown as {
    demo: { navigate: (page: 'workbench') => void; setWorkflowId: (id: string) => void };
    scope: unknown;
  };
  expect(props.scope).toBe(mockScope);
  act(() => {
    props.demo.navigate('workbench');
    props.demo.setWorkflowId('real-b');
  });
  expect(Object.fromEntries(new URLSearchParams(history.location.search))).toMatchObject({
    view: 'issues',
    workflowId: 'real-b',
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

it.each(items)(
  '%s shows introduction for a successfully loaded empty collection without mounting readers',
  async (id) => {
    read.mockResolvedValue([]);
    history.replace(`/evolution-factory/task-escort?view=${id}&period=30d&demo=1&keep=yes#detail`);
    const before = history.location;
    render(<TaskEscortPage />);
    expect(await screen.findByRole('heading', { name: /让任务执行有迹可循/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创建第一个工作流' })).toBeEnabled();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByTestId('parity-leaf')).not.toBeInTheDocument();
    expect(Dashboard).not.toHaveBeenCalled();
    expect(history.location).toBe(before);
    expect(screen.getByRole('button', { name: '产品导航' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '工作区' })).toBeDisabled();
    expect(screen.getByRole('region', { name: '任务护航内容' }).textContent).not.toMatch(/DEMO|无工作流/i);
    expect(screen.queryByRole('button', { name: /示例|演示/ })).not.toBeInTheDocument();
  },
);

it('the default Dashboard introduces empty collections and opens only the real creation dialog', async () => {
  read.mockResolvedValue([]);
  render(<TaskEscortPage />);
  fireEvent.click(await screen.findByRole('button', { name: '创建第一个工作流' }));
  expect(screen.getByRole('dialog', { name: '新建工作流' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '空白工作流' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '基于已有工作流' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'AI 生成' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(history.location.search).toBe('');
});

it('Dashboard distinguishes pending, failed, successfully empty and revoked identity without empty flashes', async () => {
  let resolve!: (value: typeof rows) => void;
  read.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const { rerender } = render(<TaskEscortPage />);
  expect(screen.getByRole('status')).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
  expect(Dashboard).not.toHaveBeenCalled();
  await act(async () => resolve([]));
  fireEvent.click(screen.getByRole('button', { name: '创建第一个工作流' }));
  Object.assign(mockScope, { ready: false, loading: true });
  rerender(<TaskEscortPage />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toBeInTheDocument();
  Object.assign(mockScope, { loading: false, error: '身份不可用' });
  rerender(<TaskEscortPage />);
  expect(screen.getByText('身份不可用')).toBeInTheDocument();
  Object.assign(mockScope, { error: undefined });
  rerender(<TaskEscortPage />);
  expect(screen.getByText('工作区尚未就绪')).toBeInTheDocument();
  expect(screen.queryByText('暂无可访问的工作流')).not.toBeInTheDocument();
  Object.assign(mockScope, { ready: true });
  read.mockRejectedValueOnce(new Error('403 forbidden'));
  rerender(<TaskEscortPage />);
  expect(await screen.findByText('403 forbidden')).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
  expect(Dashboard).not.toHaveBeenCalled();
  read.mockResolvedValueOnce([]);
  fireEvent.click(screen.getByRole('button', { name: '重试读取' }));
  expect(await screen.findByRole('heading', { name: /让任务执行有迹可循/ })).toBeInTheDocument();
});

it.each(['missing', ''])(
  'Dashboard retains invalid explicit selection workflowId=%j even on an empty list',
  async (id) => {
    read.mockResolvedValue([]);
    history.replace(`/evolution-factory/task-escort?workflowId=${id}&keep=yes#detail`);
    render(<TaskEscortPage />);
    expect(await screen.findByText(/所选工作流不存在或当前身份无权访问/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
    expect(Dashboard).not.toHaveBeenCalled();
    expect(history.location.search).toContain('keep=yes');
    expect(history.location.hash).toBe('#detail');
  },
);

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

it.each(items)(
  '%s opens product navigation over real data and returns without rewriting selection, query or hash',
  async (id) => {
    const search = `?view=${id}&workflowId=real-a&runId=actual-run&analysisId=actual-analysis&nodeId=actual-node&period=30d&host=yes&host=other`;
    history.replace(`/evolution-factory/task-escort${search}#detail`);
    const { container } = render(<TaskEscortPage />);
    if (id === 'dashboard') await screen.findByRole('heading', { name: '数据大盘' });
    else await screen.findByTestId('parity-leaf');
    const readsBefore = mockLeafReads.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '产品导航' }));
    expect(await screen.findByRole('heading', { name: /让任务执行有迹可循/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '产品导航' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '工作区' })).toBeEnabled();
    expect(new URLSearchParams(history.location.search).get('productNavigation')).toBe('1');
    const underlying = new URLSearchParams(history.location.search);
    underlying.delete('productNavigation');
    expect(underlying.toString()).toBe(search.slice(1));
    expect(history.location.hash).toBe('#detail');
    expect(container.textContent).not.toMatch(/DEMO|无工作流/i);
    expect(container.querySelector('.task-escort-workspace-root')).not.toHaveClass('task-escort-editor');
    expect(container.querySelector('.guard-sidebar')).not.toHaveClass('guard-sidebar-compact');
    expect(screen.getByRole('region', { name: '任务护航内容' })).toHaveClass('page-scroll', 'task-escort-introduction-page');
    expect(screen.queryByTestId('parity-leaf')).not.toBeInTheDocument();
    expect(mockLeafReads).toHaveBeenCalledTimes(readsBefore);
    expect(read).toHaveBeenCalledTimes(1);
    expect(taskEscortService.listWorkflows).not.toHaveBeenCalled();
    expect(taskEscortService.listFlowRuns).not.toHaveBeenCalled();
    expect(workflowAuthoringService.createBlank).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '工作区' }));
    expect(history.location.search).toBe(search);
    expect(history.location.hash).toBe('#detail');
    const destination = id === 'dashboard'
      ? screen.getByRole('heading', { name: '数据大盘' })
      : await screen.findByTestId('parity-leaf');
    expect(destination).toHaveTextContent(id === 'dashboard' ? '数据大盘' : `${id}:real-a:actual-run`);
    expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
    expect(container.querySelector('.task-escort-workspace-root')).toHaveClass(
      id === 'editor' ? 'task-escort-editor' : 'task-escort-workspace-root',
    );
  },
);

it('product navigation preserves the implicit default 运行概览 without adding a view or workflow selection', async () => {
  history.replace('/evolution-factory/task-escort?host=yes#detail');
  render(<TaskEscortPage />);
  await screen.findByTestId('parity-leaf');
  fireEvent.click(screen.getByRole('button', { name: '产品导航' }));
  expect(new URLSearchParams(history.location.search).has('view')).toBe(false);
  expect(new URLSearchParams(history.location.search).has('workflowId')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: '工作区' }));
  expect(history.location.search).toBe('?host=yes');
  expect(history.location.hash).toBe('#detail');
  expect(screen.getByTestId('parity-leaf')).toBeInTheDocument();
});

it('hides the admin-only 数据大盘 and redirects a non-admin deep link to 运行概览', async () => {
  dashboardAccess.mockReturnValue('denied');
  history.replace('/evolution-factory/task-escort?view=dashboard');
  render(<TaskEscortPage />);
  await waitFor(() => expect(new URLSearchParams(history.location.search).get('view')).toBe('runs'));
  const nav = screen.getByRole('navigation', { name: '任务护航导航' });
  expect(within(nav).queryByRole('button', { name: '数据大盘' })).not.toBeInTheDocument();
  expect(within(nav).getAllByRole('button')).toHaveLength(5);
});

it.each(items)('%s sidebar destination leaves product navigation and mounts the actual page', async (id, label) => {
  history.replace('/evolution-factory/task-escort?view=runs&workflowId=real-a&runId=actual-run&productNavigation=1&host=yes#detail');
  render(<TaskEscortPage />);
  await screen.findByRole('heading', { name: /让任务执行有迹可循/ });
  fireEvent.click(within(screen.getByRole('navigation', { name: '任务护航导航' })).getByRole('button', { name: label }));
  expect(new URLSearchParams(history.location.search).has('productNavigation')).toBe(false);
  expect(new URLSearchParams(history.location.search).get('view')).toBe(id);
  expect(new URLSearchParams(history.location.search).get('workflowId')).toBe('real-a');
  expect(new URLSearchParams(history.location.search).get('runId')).toBe('actual-run');
  expect(history.location.search).toContain('host=yes');
  expect(history.location.hash).toBe('#detail');
  const destination = id === 'dashboard'
    ? screen.getByRole('heading', { name: '数据大盘' })
    : await screen.findByTestId('parity-leaf');
  expect(destination).toHaveTextContent(id === 'dashboard' ? '数据大盘' : `${id}:real-a:actual-run`);
  expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
});

it.each([false, true])('confirmed creation exits product navigation and opens the actual new workflow; empty=%s', async (empty) => {
  read.mockResolvedValueOnce(empty ? [] : rows);
  history.replace(`/evolution-factory/task-escort?view=runs${empty ? '' : '&workflowId=real-a&runId=actual-run'}&productNavigation=1&host=yes#detail`);
  const created = { id: 'created-flow', title: '新工作流', version: '1.0.0', nodes: [] };
  jest.mocked(workflowAuthoringService.createBlank).mockResolvedValueOnce(created);
  render(<TaskEscortPage />);
  await screen.findByRole('heading', { name: /让任务执行有迹可循/ });
  fireEvent.click(screen.getByRole('button', { name: empty ? '创建第一个工作流' : '新建工作流' }));
  expect(workflowAuthoringService.createBlank).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('textbox', { name: '工作流 ID' }), { target: { value: created.id } });
  fireEvent.change(screen.getByRole('textbox', { name: '工作流名称' }), { target: { value: created.title } });
  read.mockResolvedValue([{ ...rows[0], ...created }]);
  fireEvent.click(screen.getByRole('button', { name: '创建' }));
  expect(await screen.findByTestId('parity-leaf')).toHaveTextContent('editor:created-flow:');
  expect(workflowAuthoringService.createBlank).toHaveBeenCalledWith(
    { workflowId: created.id, title: created.title },
    { user_id: 'user-a' },
    expect.any(AbortSignal),
  );
  expect(new URLSearchParams(history.location.search).has('productNavigation')).toBe(false);
  expect(new URLSearchParams(history.location.search).has('runId')).toBe(false);
  expect(history.location.search).toContain('host=yes');
  expect(history.location.hash).toBe('#detail');
  expect(read).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
});

it.each(['403 forbidden', '工作流列表格式无效'])('manual guide marker never masks workflow read failure: %s', async (message) => {
  read.mockRejectedValueOnce(new Error(message));
  history.replace('/evolution-factory/task-escort?view=runs&productNavigation=1');
  render(<TaskEscortPage />);
  expect(await screen.findByText(message)).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
  expect(screen.queryByTestId('parity-leaf')).not.toBeInTheDocument();
  expect(Dashboard).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '重试读取' }));
  expect(await screen.findByRole('heading', { name: /让任务执行有迹可循/ })).toBeInTheDocument();
});

it.each(['missing', ''])('manual guide marker cannot hide inaccessible selection workflowId=%j', async (id) => {
  history.replace(`/evolution-factory/task-escort?productNavigation=1&workflowId=${id}`);
  render(<TaskEscortPage />);
  expect(await screen.findByText(/所选工作流不存在或当前身份无权访问/)).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
  expect(mockLeafReads).not.toHaveBeenCalled();
  expect(Dashboard).not.toHaveBeenCalled();
});

it('manual introduction never upgrades identity readiness and closes creation on identity revocation', async () => {
  history.replace('/evolution-factory/task-escort?productNavigation=1&workflowId=real-a');
  Object.assign(mockScope, { ready: false, loading: true });
  const { rerender } = render(<TaskEscortPage />);
  expect(screen.getByRole('status')).toBeInTheDocument();
  expect(read).not.toHaveBeenCalled();
  expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
  Object.assign(mockScope, { ready: true, loading: false });
  rerender(<TaskEscortPage />);
  await screen.findByRole('heading', { name: /让任务执行有迹可循/ });
  fireEvent.click(screen.getByRole('button', { name: '新建工作流' }));
  expect(screen.getByRole('dialog', { name: '新建工作流' })).toBeInTheDocument();
  Object.assign(mockScope, { ready: false, loading: false, error: '身份不可用' });
  rerender(<TaskEscortPage />);
  expect(screen.getByText('身份不可用')).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
  expect(mockLeafReads).not.toHaveBeenCalled();
  expect(Dashboard).not.toHaveBeenCalled();
  expect(workflowAuthoringService.createBlank).not.toHaveBeenCalled();
  Object.assign(mockScope, { error: undefined });
  rerender(<TaskEscortPage />);
  expect(screen.getByText('工作区尚未就绪')).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
});

it('opening product navigation from the dirty editor uses the existing history guard before changing layout', async () => {
  mockEditorDirty = true;
  history.replace('/evolution-factory/task-escort?view=editor&workflowId=real-a&runId=actual-run#detail');
  const { container } = render(<TaskEscortPage />);
  await screen.findByTestId('parity-leaf');
  const before = history.location;
  fireEvent.click(screen.getByRole('button', { name: '产品导航' }));
  expect(history.location).toBe(before);
  expect(screen.getByRole('dialog', { name: '未保存编辑' })).toBeInTheDocument();
  expect(container.querySelector('.task-escort-workspace-root')).toHaveClass('task-escort-editor');
  expect(container.querySelector('.guard-sidebar')).toHaveClass('guard-sidebar-compact');
  fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect(history.location).toBe(before);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '产品导航' }));
  fireEvent.click(screen.getByRole('button', { name: '放弃并离开' }));
  expect(await screen.findByRole('heading', { name: /让任务执行有迹可循/ })).toBeInTheDocument();
  expect(container.querySelector('.task-escort-workspace-root')).not.toHaveClass('task-escort-editor');
  expect(container.querySelector('.guard-sidebar')).not.toHaveClass('guard-sidebar-compact');
  expect(history.location.hash).toBe('#detail');
  expect(new URLSearchParams(history.location.search).get('runId')).toBe('actual-run');
});

it.each(['0', 'true', 'other'])('ignores unknown product-navigation marker values: %s', async (value) => {
  history.replace(`/evolution-factory/task-escort?view=runs&workflowId=real-a&productNavigation=${value}`);
  render(<TaskEscortPage />);
  expect(await screen.findByTestId('parity-leaf')).toHaveTextContent('runs:real-a:');
  expect(screen.queryByRole('heading', { name: /让任务执行有迹可循/ })).not.toBeInTheDocument();
});
