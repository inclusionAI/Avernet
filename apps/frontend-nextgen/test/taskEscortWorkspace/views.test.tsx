/** @jest-environment jsdom */
import type { Issue, PageId, TaskEscortWorkspaceController } from '@/domain/taskEscortWorkspace/types';
import { getWorkspaceSupport } from '@/domain/taskEscortWorkspace/workspace';
import { Experiences } from '@/pages/TaskEscort/Experiences';
import { Issues } from '@/pages/TaskEscort/Issues';
import { GuardSidebar, pages } from '@/pages/TaskEscort/Navigation';
import { Runs } from '@/pages/TaskEscort/Runs';
import { Settings } from '@/pages/TaskEscort/Settings';
import { Workbench } from '@/pages/TaskEscort/Workbench';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

const issue: Issue = {
  id: 'issue-1',
  title: '采集超时',
  description: '请求中断',
  workflowId: 'wf-1',
  workflowName: '采集工作流',
  node: '请求节点',
  severity: 'high',
  status: 'open',
  affectedRuns: 5,
  category: '可靠性',
  rootCause: '限流',
  recommendation: '增加退避',
  before: 'retry: 0',
  after: 'retry: 2',
  evidence: ['HTTP 429'],
  confidence: 0.9,
};
const onApply = jest.fn();
const onVerify = jest.fn();
const onSave = jest.fn();
const onNavigate = jest.fn();
const onWorkflow = jest.fn();
const onNotice = jest.fn();

Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.scrollIntoView ??= () => undefined;

type PrototypePageId = Exclude<PageId, 'dashboard' | 'editor'>;

function Harness({
  view = 'issues',
  empty = false,
  mode = 'demo',
}: {
  view?: PrototypePageId;
  empty?: boolean;
  mode?: 'demo' | 'live';
}) {
  const [issues, setIssues] = useState<Issue[]>(
    empty ? [] : [issue, { ...issue, id: 'done', title: '完成记录', status: 'verified' }],
  );
  const [query, setQuery] = useState('');
  const [workflowId, setWorkflow] = useState('all');
  const [issueFilter, setIssueFilter] = useState('all');
  const [runFilter, setRunFilter] = useState('all');
  const [selectedIssue, setSelectedIssue] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [experiences, setExperiences] = useState([
    {
      id: 'exp-1',
      title: '退避重试',
      description: '处理限流',
      category: '可靠性',
      workflowName: '采集工作流',
      reuseCount: 2,
      verified: true,
      sourceIssueId: 'history',
    },
  ]);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [settings, setSettings] = useState({
    enabled: true,
    autoAnalyze: true,
    humanReview: true,
    notifications: false,
    recurrence: true,
    sensitivity: 'balanced',
    webhook: '',
  });
  const demo: TaskEscortWorkspaceController = {
    page: view,
    navigate: onNavigate,
    period: '7d',
    setPeriod: jest.fn(),
    mode,
    loading: false,
    error: null,
    refresh: jest.fn(),
    support: getWorkspaceSupport(mode),
    metrics: {
      runs: 10,
      successRate: 90,
      anomalies: 1,
      p50: 30,
      running: 1,
      waiting: 1,
      blocked: 0,
      queued: 0,
      series: [2, 3],
      successSeries: [90, 90],
      labels: ['11/16', '11/17'],
    },
    workflows: [
      {
        id: 'wf-1',
        name: '采集工作流',
        description: '演示工作流',
        bot: '示例助手',
        version: 'v1',
        successRate: 90,
        runCount: 10,
        status: 'healthy',
        updatedAt: '10:00',
        category: '演示',
      },
    ],
    runs: empty
      ? []
      : [
          {
            id: 'run-1',
            workflowId: 'wf-1',
            name: '采集工作流',
            bot: '示例助手',
            status: 'failed',
            duration: 90,
            startedAt: '2026-11-17T10:00:00',
            trigger: '手动',
            node: '请求节点',
          },
        ],
    issues,
    experiences: empty ? [] : experiences,
    query,
    setQuery,
    issueFilter,
    setIssueFilter,
    runFilter,
    setRunFilter,
    selectedIssue,
    setSelectedIssue,
    selectedRun,
    setSelectedRun,
    workflowId,
    setWorkflowId: (id: string) => {
      onWorkflow(id);
      setWorkflow(id);
    },
    applySuggestion: (id: string) => {
      onApply(id);
      setIssues((all) => all.map((entry) => (entry.id === id ? { ...entry, status: 'applied' } : entry)));
    },
    verifyIssue: (id: string) => {
      onVerify(id);
      setIssues((all) => all.map((entry) => (entry.id === id ? { ...entry, status: 'verified' } : entry)));
    },
    saveExperience: (id: string) => {
      onSave(id);
      setExperiences((all) => [...all, { ...all[0], id: 'saved', sourceIssueId: id }]);
    },
    settings,
    settingsDirty,
    updateSettings: (patch: Partial<typeof settings>) => {
      setSettingsDirty(true);
      setSettings((current) => ({ ...current, ...patch }));
    },
    saveSettings: () => false,
    notice: onNotice,
    reset: jest.fn(),
  };
  const views = {
    issues: Issues,
    workbench: Workbench,
    runs: Runs,
    experiences: Experiences,
    settings: Settings,
  };
  const View = views[view];
  return (
    <>
      <GuardSidebar page={view === 'workbench' ? 'issues' : view} navigate={onNavigate} />
      <View demo={demo} />
    </>
  );
}

beforeEach(() => jest.clearAllMocks());

test('exports exactly six reference navigation entries without changing the global menus', () => {
  expect(pages.map((entry) => entry.id)).toEqual(['dashboard', 'runs', 'issues', 'experiences', 'editor', 'settings']);
  render(<Harness view="workbench" />);
  expect(screen.getByRole('navigation', { name: '任务护航导航' })).toBeInTheDocument();
  expect(within(screen.getByRole('navigation', { name: '任务护航导航' })).getAllByRole('button')).toHaveLength(6);
  expect(screen.queryByText('研发效能')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '数据大盘' }));
  expect(onNavigate).toHaveBeenCalledWith('dashboard');
});

test('issues search combines with state filters and clearing recovers empty results', async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.type(screen.getByRole('textbox', { name: '搜索异常、工作流或日志证据…' }), 'missing');
  expect(screen.getByText('没有符合条件的异常')).toBeInTheDocument();
  await user.click(within(screen.getByTestId('issues-view')).getAllByRole('button', { name: '清除筛选' })[0]);
  expect(screen.getAllByTestId('issue-card')).toHaveLength(2);
  await user.click(screen.getByRole('tab', { name: /已验证/ }));
  expect(screen.getAllByTestId('issue-card')).toHaveLength(1);
});

test('confirmation requires acknowledgement, changes only the requested lifecycle step and restores focus', async () => {
  const user = userEvent.setup();
  render(<Harness />);
  const opener = screen.getByRole('button', { name: '查看异常详情：采集超时' });
  await user.click(opener);
  expect(screen.queryByRole('button', { name: '保存为经验' })).not.toBeInTheDocument();
  const apply = screen.getByRole('button', { name: '应用建议' });
  await user.click(apply);
  const confirm = screen.getByRole('button', { name: '确认应用（仅演示）' });
  expect(confirm).toBeDisabled();
  await user.click(screen.getByRole('switch', { name: '确认应用建议的演示安全范围' }));
  await user.click(confirm);
  expect(onApply).toHaveBeenCalledWith('issue-1');
  expect(onVerify).not.toHaveBeenCalled();
  expect(onSave).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: '人工验证通过' }));
  await user.click(screen.getByRole('switch', { name: '确认已人工检查修复结果' }));
  await user.click(screen.getByRole('button', { name: '确认人工验证通过' }));
  await user.click(screen.getByRole('button', { name: '保存为经验' }));
  expect(onSave).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: '已保存经验' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: '关闭' }));
  await waitFor(() => expect(opener).toHaveFocus());
});

test('workbench retains its prototype history while the shared sidebar marks pending count unknown', async () => {
  const user = userEvent.setup();
  render(<Harness view="workbench" />);
  expect(screen.getAllByTestId('triage-row')).toHaveLength(1);
  expect(screen.queryByRole('button', { name: '工作台' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('tab', { name: /已完成/ }));
  expect(screen.getByTestId('triage-row')).toHaveTextContent('完成记录');
  await user.click(screen.getByRole('button', { name: '查看采集工作流的运行' }));
  expect(onNavigate).toHaveBeenCalledWith('runs');
  expect(onWorkflow).toHaveBeenCalledWith('wf-1');
});

test('run selection displays read-only graph, changes selected node and restores focus', async () => {
  const user = userEvent.setup();
  render(<Harness view="runs" />);
  const opener = screen.getByRole('button', { name: '查看运行 run-1 详情' });
  await user.click(opener);
  expect(screen.getByRole('heading', { name: '节点路径' })).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: /准备上下文/ }));
  expect(screen.getByRole('dialog')).toHaveTextContent('准备上下文');
  await user.click(screen.getByRole('button', { name: '关闭详情' }));
  await waitFor(() => expect(opener).toHaveFocus());
});

test('experience details are reference only and return focus to their opener', async () => {
  const user = userEvent.setup();
  render(<Harness view="experiences" />);
  const opener = screen.getByRole('button', { name: '查看经验：退避重试' });
  await user.click(opener);
  expect(screen.getByRole('dialog')).toHaveTextContent('仅供参考');
  await user.click(screen.getByRole('button', { name: /参考这条经验/ }));
  expect(onNotice).toHaveBeenCalledWith(expect.stringContaining('未应用任何变更'));
  expect(onApply).not.toHaveBeenCalled();
  await waitFor(() => expect(opener).toHaveFocus());
});

test.each<[PrototypePageId, string]>([
  ['issues', '没有符合条件的异常'],
  ['runs', '没有匹配的运行记录'],
  ['experiences', '没有匹配的经验'],
])('%s renders the project empty state', (view, title) => {
  render(<Harness view={view} empty />);
  expect(screen.getByText(title)).toBeInTheDocument();
});

test('prototype settings switches remain local; webhook remains text-only', async () => {
  const user = userEvent.setup();
  render(<Harness view="settings" />);
  await user.click(screen.getByRole('switch', { name: '开启任务护航' }));
  expect(screen.getByRole('switch', { name: '开启任务护航' })).not.toBeChecked();
  expect(screen.queryByText('模拟护航已暂停')).not.toBeInTheDocument();
  await user.click(screen.getByRole('tab', { name: /通知与回调/ }));
  await user.type(screen.getByRole('textbox', { name: '回调地址' }), 'https://example.invalid/hook');
  expect(screen.getByRole('textbox', { name: '回调地址' })).toHaveValue('https://example.invalid/hook');
  await user.click(screen.getByRole('button', { name: '保存设置' }));
  expect(screen.getByText('有未保存的演示设置')).toBeInTheDocument();
  await user.click(screen.getByRole('tab', { name: /自动化设置/ }));
  await user.click(screen.getByRole('tab', { name: /通知与回调/ }));
  expect(screen.getByRole('textbox', { name: '回调地址' })).toHaveValue('https://example.invalid/hook');
  expect(onApply).not.toHaveBeenCalled();
});

test('live detail cannot expose demo write actions even if mounted accidentally', async () => {
  const user = userEvent.setup();
  render(<Harness mode="live" />);
  await user.click(screen.getByRole('button', { name: '查看异常详情：采集超时' }));
  expect(screen.queryByRole('button', { name: '应用建议' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '保存为经验' })).not.toBeInTheDocument();
  expect(onApply).not.toHaveBeenCalled();
});

test('run status and workflow selects combine with query; clearing restores records', async () => {
  const user = userEvent.setup();
  render(<Harness view="runs" />);
  fireEvent.keyDown(screen.getByRole('combobox', { name: '运行状态筛选' }), { key: 'Enter' });
  await user.click(await screen.findByRole('option', { name: '运行成功' }));
  expect(screen.getByText('没有匹配的运行记录')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '清除筛选' }));
  fireEvent.keyDown(screen.getByRole('combobox', { name: '运行工作流筛选' }), { key: 'Enter' });
  await user.click(await screen.findByRole('option', { name: '采集工作流' }));
  expect(onWorkflow).toHaveBeenLastCalledWith('wf-1');
  await user.type(screen.getByRole('textbox', { name: '搜索运行 ID、工作流、Bot 或节点…' }), 'missing');
  expect(screen.getByText('没有匹配的运行记录')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '清除筛选' }));
  expect(screen.getByTestId('run-row')).toHaveTextContent('run-1');
});

test('experience category select and query can be reset from an empty library result', async () => {
  const user = userEvent.setup();
  render(<Harness view="experiences" />);
  fireEvent.keyDown(screen.getByRole('combobox', { name: '经验分类筛选' }), { key: 'Enter' });
  await user.click(await screen.findByRole('option', { name: '可靠性' }));
  await user.type(screen.getByRole('textbox', { name: '搜索经验标题、分类或工作流…' }), 'missing');
  expect(screen.getByText('没有匹配的经验')).toBeInTheDocument();
  await user.click(within(screen.getByTestId('experiences-view')).getAllByRole('button', { name: '清除筛选' })[0]);
  expect(screen.getByTestId('experience-card')).toHaveTextContent('退避重试');
});

test('workbench reset clears query and restores pending scope', async () => {
  const user = userEvent.setup();
  render(<Harness view="workbench" />);
  await user.type(screen.getByRole('textbox', { name: '搜索异常、节点或证据…' }), 'missing');
  expect(screen.getByText('没有符合条件的异常')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '重置' }));
  expect(screen.getAllByTestId('triage-row')).toHaveLength(1);
});

test('cancelled confirmation never calls the controller and reopens unacknowledged', async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.click(screen.getByRole('button', { name: '查看异常详情：采集超时' }));
  await user.click(screen.getByRole('button', { name: '应用建议' }));
  await user.click(screen.getByRole('switch', { name: '确认应用建议的演示安全范围' }));
  await user.click(screen.getByRole('button', { name: '取消' }));
  await user.click(screen.getByRole('button', { name: '应用建议' }));
  expect(screen.getByRole('button', { name: '确认应用（仅演示）' })).toBeDisabled();
  expect(onApply).not.toHaveBeenCalled();
});

test('detail close focuses the view fallback when its filtered opener disappears', async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.click(screen.getByRole('tab', { name: /待处理/ }));
  await user.click(screen.getByRole('button', { name: '查看异常详情：采集超时' }));
  await user.click(screen.getByRole('button', { name: '应用建议' }));
  await user.click(screen.getByRole('switch', { name: '确认应用建议的演示安全范围' }));
  await user.click(screen.getByRole('button', { name: '确认应用（仅演示）' }));
  await user.click(screen.getByRole('button', { name: '关闭' }));
  await waitFor(() => expect(screen.getByTestId('issues-view')).toHaveFocus());
});

test('live sidebar marks unsupported pending counts unknown instead of presenting synthetic zero totals', () => {
  render(<Harness mode="live" />);
  expect(screen.queryByRole('button', { name: '工作台' })).not.toBeInTheDocument();
  expect(screen.queryByText(/样本工作流|只读观察|模拟护航/)).not.toBeInTheDocument();
});
