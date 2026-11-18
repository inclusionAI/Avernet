/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import type { TaskEscortWorkspaceController } from '@/domain/taskEscortWorkspace';
import { TaskEscortEditorProvider } from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceEditorContext';
import { Editor } from '@/pages/TaskEscort/Editor';
import { WorkspaceHeader } from '@/pages/TaskEscort/WorkspaceHeader';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { history } from '@umijs/max';
import { useState } from 'react';

jest.mock('@umijs/max', () => ({
  history: jest
    .requireActual('history')
    .createMemoryHistory({ initialEntries: ['/evolution-factory/task-escort?demo=1&view=editor'] }),
}));
const notice = jest.fn();
const refresh = jest.fn();
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
beforeAll(() => {
  HTMLElement.prototype.scrollIntoView = jest.fn();
});
afterAll(() => {
  HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
});
function EditorHarnessControls({
  toggle,
  reset,
  switchWorkflow,
}: {
  toggle: () => void;
  reset: () => void;
  switchWorkflow: () => void;
}) {
  // Test-only controls mimic root lifecycle; production navigation is independently tested through Umi history.
  return (
    <div>
      <button type="button" onClick={toggle}>
        切换编辑器挂载
      </button>
      <button type="button" onClick={reset}>
        外部重置
      </button>
      <button type="button" onClick={() => history.push('/evolution-factory/task-escort?demo=1&view=issues')}>
        转到问题视图
      </button>
      <button type="button" onClick={switchWorkflow}>
        外部工作流切换
      </button>
    </div>
  );
}
function Harness({ mode = 'demo' }: { mode?: 'live' | 'demo' }) {
  const [workflowId, setWorkflowId] = useState('one');
  const [editor, setEditor] = useState(true);
  const [reset, setReset] = useState(0);
  const controller: TaskEscortWorkspaceController = {
    mode,
    workflowId,
    setWorkflowId,
    notice,
    workflows: [
      {
        id: 'one',
        name: '工作流一',
        description: '',
        bot: 'Bot',
        version: 'v2.4',
        successRate: 1,
        runCount: 1,
        status: 'healthy',
        updatedAt: null,
        category: '',
      },
      {
        id: 'two',
        name: '工作流二',
        description: '',
        bot: 'Bot',
        version: 'v2.4',
        successRate: 1,
        runCount: 1,
        status: 'healthy',
        updatedAt: null,
        category: '',
      },
    ],
    page: 'editor',
    navigate: jest.fn(),
    period: '7d',
    setPeriod: (period) => history.push(`/evolution-factory/task-escort?demo=1&view=editor&period=${period}`),
    metrics: {
      runs: 0,
      successRate: 0,
      anomalies: 0,
      p50: 0,
      running: 0,
      waiting: 0,
      blocked: 0,
      queued: 0,
      series: [],
      successSeries: [],
      labels: [],
    },
    runs: [],
    issues: [],
    experiences: [],
    query: '',
    setQuery: jest.fn(),
    issueFilter: 'all',
    setIssueFilter: jest.fn(),
    runFilter: 'all',
    setRunFilter: jest.fn(),
    selectedIssue: null,
    setSelectedIssue: jest.fn(),
    selectedRun: null,
    setSelectedRun: jest.fn(),
    applySuggestion: jest.fn(),
    verifyIssue: jest.fn(),
    saveExperience: jest.fn(),
    settings: {
      enabled: true,
      autoAnalyze: true,
      humanReview: true,
      notifications: false,
      recurrence: true,
      sensitivity: 'balanced',
      webhook: '',
    },
    settingsDirty: false,
    updateSettings: jest.fn(),
    saveSettings: () => true,
    reset: jest.fn(),
    loading: false,
    error: null,
    refresh,
    support: Object.fromEntries(
      ['dashboard', 'workbench', 'runs', 'issues', 'experiences', 'editor', 'settings'].map((page) => [
        page,
        { available: true },
      ]),
    ) as TaskEscortWorkspaceController['support'],
  };
  const headerWorkflow = { id: workflowId, title: workflowId, runCount: null, lastStatus: null, ownerId: null };
  const headerContext: TaskEscortParityContext = {
    scopeKey: 'test',
    ready: true,
    workflowId,
    workflow: headerWorkflow,
    query: {},
    navigate: controller.navigate,
    refreshWorkflows: refresh,
  };
  return (
    <TaskEscortEditorProvider workspaceKey="test" mode={mode} resetKey={reset}>
      <EditorHarnessControls
        toggle={() => setEditor(!editor)}
        reset={() => setReset(reset + 1)}
        switchWorkflow={() => setWorkflowId(workflowId === 'one' ? 'two' : 'one')}
      />
      <WorkspaceHeader
        page="editor"
        context={headerContext}
        workflows={[headerWorkflow]}
        selectWorkflow={setWorkflowId}
      />
      {editor ? <Editor demo={controller} /> : <div>其他视图</div>}
    </TaskEscortEditorProvider>
  );
}
beforeEach(() => {
  notice.mockReset();
  refresh.mockReset();
  history.replace('/evolution-factory/task-escort?demo=1&view=editor&period=7d');
});

it('renders graph/properties, maps palette to the correct nodes and disables unsupported branch', () => {
  render(<Harness />);
  expect(screen.getByLabelText('节点名称')).toHaveValue('数据采集');
  expect(screen.getByRole('button', { name: /条件分支/ })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: /智能体/ }));
  expect(screen.getByLabelText('节点 ID')).toHaveValue('analyze');
  fireEvent.click(screen.getByRole('button', { name: /工具调用/ }));
  expect(screen.getByLabelText('节点 ID')).toHaveValue('fetch');
  fireEvent.click(screen.getByRole('button', { name: /触发器/ }));
  expect(screen.getByLabelText('节点 ID')).toHaveValue('start');
});

it('keeps YAML preview in sync, supports undo/redo and saves only to workspace memory', () => {
  render(<Harness />);
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '调整后的采集' } });
  expect(screen.getByText(/未保存/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '撤销编辑' }));
  expect(screen.getByLabelText('节点名称')).toHaveValue('数据采集');
  fireEvent.click(screen.getByRole('button', { name: '重做编辑' }));
  expect(screen.getByLabelText('节点名称')).toHaveValue('调整后的采集');
  fireEvent.click(screen.getByRole('button', { name: '分屏' }));
  expect(screen.getByTestId('editor-yaml')).toHaveTextContent('调整后的采集');
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  expect(screen.queryByText(/未保存/)).not.toBeInTheDocument();
  expect(notice).toHaveBeenLastCalledWith(expect.stringContaining('未提交到服务器'));
  fireEvent.click(screen.getByRole('button', { name: '切换编辑器挂载' }));
  fireEvent.click(screen.getByRole('button', { name: '切换编辑器挂载' }));
  expect(screen.getByLabelText('节点名称')).toHaveValue('调整后的采集');
});

it('isolates saved workflows and clears drafts when reset outside the editor', () => {
  render(<Harness />);
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '已保存的一' } });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  fireEvent.click(screen.getByRole('button', { name: '外部工作流切换' }));
  expect(screen.getByLabelText('节点名称')).toHaveValue('数据采集');
  fireEvent.click(screen.getByRole('button', { name: '外部工作流切换' }));
  expect(screen.getByLabelText('节点名称')).toHaveValue('已保存的一');
  fireEvent.click(screen.getByRole('button', { name: '切换编辑器挂载' }));
  fireEvent.click(screen.getByRole('button', { name: '外部重置' }));
  fireEvent.click(screen.getByRole('button', { name: '切换编辑器挂载' }));
  expect(screen.getByLabelText('节点名称')).toHaveValue('数据采集');
});

it('rejects invalid save/publish and keeps unsaved edits, but simulates a valid local publish', () => {
  render(<Harness />);
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: ' ' } });
  fireEvent.change(screen.getByLabelText('超时 / 秒'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  expect(notice).toHaveBeenLastCalledWith(expect.stringContaining('校验未通过'));
  expect(screen.getByText(/未保存/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '模拟发布' }));
  expect(within(screen.getByRole('dialog')).getByRole('button', { name: '确认模拟发布' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '采集' } });
  fireEvent.change(screen.getByLabelText('超时 / 秒'), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: '模拟发布' }));
  fireEvent.click(screen.getByRole('button', { name: '确认模拟发布' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('v2.5')).toBeInTheDocument();
  expect(notice).toHaveBeenLastCalledWith(expect.stringContaining('模拟发布完成'));
});

it('protects router query navigation and restores saved baseline when leaving is confirmed', () => {
  render(<Harness />);
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '未保存的' } });
  const location = history.location;
  act(() => history.push('/evolution-factory/task-escort?demo=1&view=issues'));
  expect(history.location).toBe(location);
  expect(screen.getByRole('alertdialog')).toHaveTextContent('有未保存的草稿');
  fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect(screen.getByLabelText('节点名称')).toHaveValue('未保存的');
  act(() => history.push('/outside'));
  fireEvent.click(screen.getByRole('button', { name: '放弃并离开' }));
  expect(history.location.pathname).toBe('/outside');
  expect(screen.getByLabelText('节点名称')).toHaveValue('数据采集');
});

it('does not show or expose a fabricated editor in live mode', () => {
  render(<Harness mode="live" />);
  expect(screen.queryByLabelText('节点名称')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '模拟发布' })).not.toBeInTheDocument();
  expect(screen.getByText(/编辑器仅在显式演示模式开放/)).toBeInTheDocument();
});

it('reset outside the editor clears dirty state and removes pending unload/router guards', () => {
  render(<Harness />);
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '会被重置' } });
  fireEvent.click(screen.getByRole('button', { name: '外部重置' }));
  expect(screen.getByLabelText('节点名称')).toHaveValue('数据采集');
  expect(screen.queryByText(/未保存/)).not.toBeInTheDocument();
  const event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
  act(() => history.push('/after-reset'));
  expect(history.location.pathname).toBe('/after-reset');
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
});

it('switching to live mode removes the dirty editor and its active navigation blocker', () => {
  const { rerender } = render(<Harness />);
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '演示模式修改' } });
  act(() => history.push('/blocked-before-mode-change'));
  expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  rerender(<Harness mode="live" />);
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('节点名称')).not.toBeInTheDocument();
  act(() => history.push('/after-mode-change'));
  expect(history.location.pathname).toBe('/after-mode-change');
});

it('confirms local workflow switching and restores its own saved draft after cancelling or discarding edits', async () => {
  const user = userEvent.setup();
  render(<Harness />);
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '已保存基线' } });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '未保存的工作流一修改' } });
  fireEvent.keyDown(screen.getByRole('combobox', { name: '编辑工作流' }), { key: 'Enter' });
  await user.click(await screen.findByRole('option', { name: '工作流二' }));
  expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '继续编辑' }));
  expect(screen.getByLabelText('节点名称')).toHaveValue('未保存的工作流一修改');
  expect(screen.getByRole('combobox', { name: '编辑工作流' })).toHaveTextContent('工作流一');
  fireEvent.keyDown(screen.getByRole('combobox', { name: '编辑工作流' }), { key: 'Enter' });
  await user.click(await screen.findByRole('option', { name: '工作流二' }));
  await user.click(screen.getByRole('button', { name: '放弃并离开' }));
  expect(screen.getByRole('combobox', { name: '编辑工作流' })).toHaveTextContent('工作流二');
  expect(screen.getByLabelText('节点名称')).toHaveValue('数据采集');
  fireEvent.keyDown(screen.getByRole('combobox', { name: '编辑工作流' }), { key: 'Enter' });
  await user.click(await screen.findByRole('option', { name: '工作流一' }));
  expect(screen.getByLabelText('节点名称')).toHaveValue('已保存基线');
});

it('shared header remains a real title and refresh without prototype mode, reset or period actions', async () => {
  const user = userEvent.setup();
  render(<Harness />);
  expect(screen.getByRole('heading', { name: '编辑器' })).toBeInTheDocument();
  expect(screen.getByText('编辑工作流结构、节点与执行配置')).toBeInTheDocument();
  expect(screen.queryByRole('group', { name: '时间范围' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /重置演示|开启完整演示|返回真实只读/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '刷新数据' }));
  expect(refresh).toHaveBeenCalledTimes(1);
});

it.each(['cancel', 'Escape'])(
  'restores the keyboard initiating router control when dismissing by %s',
  async (dismissal) => {
    const user = userEvent.setup();
    render(<Harness />);
    fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '键盘草稿' } });
    const opener = screen.getByRole('button', { name: '转到问题视图' });
    opener.focus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    if (dismissal === 'Escape') await user.keyboard('{Escape}');
    else await user.click(screen.getByRole('button', { name: '继续编辑' }));
    await waitFor(() => expect(opener).toHaveFocus());
    expect(screen.getByLabelText('节点名称')).toHaveValue('键盘草稿');
  },
);

it.each(['cancel', 'Escape'])(
  'restores the local workflow control or connected editor fallback when dismissing by %s',
  async (dismissal) => {
    const user = userEvent.setup();
    render(<Harness />);
    fireEvent.change(screen.getByLabelText('节点名称'), { target: { value: '工作流切换前草稿' } });
    const opener = screen.getByRole('combobox', { name: '编辑工作流' });
    opener.focus();
    await user.keyboard('{Enter}');
    await user.click(await screen.findByRole('option', { name: '工作流二' }));
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    if (dismissal === 'Escape') await user.keyboard('{Escape}');
    else await user.click(screen.getByRole('button', { name: '继续编辑' }));
    await waitFor(() => expect(document.activeElement).not.toBe(document.body));
    expect(document.activeElement?.isConnected).toBe(true);
    expect([opener, screen.getByRole('region', { name: '工作流编辑器' })]).toContain(document.activeElement);
    expect(screen.getByRole('combobox', { name: '编辑工作流' })).toHaveTextContent('工作流一');
    expect(screen.getByLabelText('节点名称')).toHaveValue('工作流切换前草稿');
  },
);
