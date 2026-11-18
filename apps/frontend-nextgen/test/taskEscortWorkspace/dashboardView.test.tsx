/** @jest-environment jsdom */
import { useTaskEscortDashboard } from '@/hooks/taskEscortWorkspace/useTaskEscortDashboard';
import { Dashboard } from '@/pages/TaskEscort/Dashboard';
import { fireEvent, render, screen, within } from '@testing-library/react';
jest.mock('@/hooks/taskEscortWorkspace/useTaskEscortDashboard', () => ({ useTaskEscortDashboard: jest.fn() }));
const hook = jest.mocked(useTaskEscortDashboard);
const resource = (data: any) => ({ data, loading: false, error: null, retry: jest.fn() });
function state() {
  return {
    range: '7d',
    rangeLabel: '近7天',
    widgetRanges: { trend: 'global', hotspots: 'global', workflows: 'global' },
    windows: { trend: { label: '近7天' }, hotspots: { label: '近7天' }, workflows: { label: '近7天' } },
    setWidgetRange: jest.fn(),
    refresh: jest.fn(),
    forbidden: false,
    overview: resource({
      totalRuns: 1234,
      successRate: 0.875,
      prevSuccessRate: null,
      failedCount: 9,
      machineDurationP50: 123000,
      statusDistribution: { running: 8, waiting: 2, blocked: 1, queued: 0 },
      evolution: {
        available: true,
        verificationAvailable: false,
        issueClusterCount: 4,
        suggestionCount: 3,
        applicationSucceededCount: 2,
        verifiedCount: null,
        recurrenceDetectedCount: null,
        appliedUnverifiedCount: null,
      },
    }),
    trend: resource({ dates: [{ date: '2026-11-17', runCount: 8, failedCount: 1, successRate: 0.875 }] }),
    hotspots: resource({
      hotspots: [
        {
          nodeLabel: '真实失败节点',
          workflowTitle: '真实流程',
          sceneName: '',
          count: 3,
          sharePct: 75,
          exampleError: 'timeout',
        },
      ],
    }),
    health: resource({
      available: true,
      workflows: [
        {
          workflowId: 'real',
          workflowTitle: '真实流程',
          released: false,
          runCount: 8,
          failedCount: 1,
          completionSuccessRate: 0.875,
          selfHealSuccessRate: null,
          machineDurationP50: 1000,
        },
      ],
    }),
    releases: resource({ available: false, workflows: [] }),
  };
}
const demo = {
  period: '7d',
  setPeriod: jest.fn(),
  navigate: jest.fn(),
  workflowId: 'ignored-workflow',
  metrics: { runs: 99999 },
  runs: [{ name: '样本不应渲染' }],
} as any;
beforeEach(() => {
  jest.clearAllMocks();
  hook.mockReturnValue(state() as any);
});
it('renders genuine admin dashboard sections and actual aggregate values, not sample lists/tabs/mode banners', () => {
  const scope = { key: 'user:space', ready: true };
  render(<Dashboard demo={demo} scope={scope} />);
  expect(hook).toHaveBeenCalledWith(scope, '7d');
  expect(screen.getByRole('heading', { name: '管理员数据大盘' })).toBeTruthy();
  for (const name of ['全局运行健康', '护航待办', '护航处理进度', '运行质量趋势', '异常热点', '工作流列表'])
    expect(screen.getByRole('heading', { name })).toBeTruthy();
  expect(screen.getByText('1,234')).toBeTruthy();
  expect(screen.getAllByText('87.5%').length).toBeGreaterThan(0);
  expect(screen.getByText('123s')).toBeTruthy();
  expect(screen.getByText(/过程计数，不是同一批次的转化率/)).toBeTruthy();
  for (const name of ['运行质量趋势时间范围', '异常热点时间范围', '工作流列表时间范围'])
    expect(screen.getByRole('combobox', { name })).toBeTruthy();
  expect(screen.getByRole('img', { name: /运行质量趋势/ })).toBeTruthy();
  expect(screen.getByRole('table', { name: '工作流列表' })).toBeTruthy();
  expect(screen.queryByText(/研发效能|模拟快照|演示模式|样本不应渲染|99999/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '进入任务护航' }));
  expect(demo.navigate).toHaveBeenCalledWith('workbench');
  fireEvent.click(screen.getByRole('button', { name: '今天' }));
  expect(demo.setPeriod).toHaveBeenCalledWith('today');
});
it('explicit forbidden replaces all dashboard data and never suggests demo fallback', () => {
  const data = state();
  data.forbidden = true;
  hook.mockReturnValue(data as any);
  render(<Dashboard demo={demo} scope={{ key: 'u:s', ready: true }} />);
  expect(screen.getByText('只有管理员可以查看全局数据大盘')).toBeTruthy();
  expect(screen.queryByText('1,234')).toBeNull();
  expect(screen.queryByText(/演示/)).toBeNull();
});
it('shows readiness failures and safe retry without exposing stale metrics', () => {
  const retry = jest.fn();
  render(<Dashboard demo={demo} scope={{ key: 'u:s', ready: false, error: '身份读取失败', retry }} />);
  expect(screen.getByText('身份读取失败')).toBeTruthy();
  expect(screen.queryByText('1,234')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '重试身份读取' }));
  expect(retry).toHaveBeenCalledTimes(1);
});
it('keeps partial widgets visible and shows independent errors/unavailable metrics, never zero fallback', () => {
  const data = state();
  data.trend = { ...resource(null), error: 'trend offline' } as any;
  hook.mockReturnValue(data as any);
  render(<Dashboard demo={demo} scope={{ key: 'u:s', ready: true }} />);
  expect(screen.getByText('trend offline')).toBeTruthy();
  expect(screen.getByText('1,234')).toBeTruthy();
  const table = screen.getByRole('table', { name: '工作流列表' });
  expect(within(table).getAllByText('—').length).toBeGreaterThan(2);
  expect(screen.getByText(/发布统计能力暂不可用/)).toBeTruthy();
});
it('renders the shared Skeleton primitive for identity and every widget loading state', () => {
  const data = state();
  for (const name of ['overview', 'trend', 'hotspots', 'health', 'releases'] as const)
    data[name] = { ...resource(null), loading: true };
  hook.mockReturnValue(data as any);
  const view = render(<Dashboard demo={demo} scope={{ key: 'u:s', ready: true }} />);
  expect(screen.getByRole('status', { name: '全局运行健康加载中' })).toBeTruthy();
  expect(screen.getByRole('status', { name: '工作流列表加载中' })).toBeTruthy();
  expect(screen.queryByText('1,234')).toBeNull();
  view.rerender(<Dashboard demo={demo} scope={{ key: 'u:s', ready: false, loading: true }} />);
  expect(screen.getByRole('status', { name: '正在确认身份' })).toBeTruthy();
});
it('keeps genuine empty widgets and unavailable health separate from successful release metrics', () => {
  const data = state();
  data.overview = resource({ ...data.overview.data, totalRuns: 0 });
  data.trend = resource({ dates: [] });
  data.hotspots = resource({ hotspots: [] });
  data.health = resource({
    available: false,
    workflows: [{ workflowId: 'must-not-leak', workflowTitle: '不可用脏数据' }],
  }) as any;
  data.releases = resource({
    available: true,
    workflows: [
      {
        workflowId: 'release-only',
        workflowTitle: '已发布真实流程',
        released: true,
        deployCount: 2,
        devCycleMs: null,
        lastDeployAt: null,
        firstRunSuccessRate: null,
      },
    ],
  });
  hook.mockReturnValue(data as any);
  render(<Dashboard demo={demo} scope={{ key: 'u:s', ready: true }} />);
  expect(screen.getByText('所选周期暂无运行')).toBeTruthy();
  expect(screen.getByText('所选周期暂无运行趋势')).toBeTruthy();
  expect(screen.getByText('所选周期暂无异常热点')).toBeTruthy();
  expect(screen.queryByText('不可用脏数据')).toBeNull();
  expect(screen.getByText('已发布真实流程')).toBeTruthy();
  const row = screen.getByText('已发布真实流程').closest('tr')!;
  expect(within(row).getAllByText('—')).toHaveLength(8);
});
it('searches and paginates genuine joined rows, then clears out-of-range paging on search', () => {
  const data = state();
  data.health = resource({
    available: true,
    workflows: Array.from({ length: 12 }, (_, i) => ({
      ...data.health.data.workflows[0],
      workflowId: `workflow-${i}`,
      workflowTitle: `Genuine workflow ${i}`,
      completionSuccessRate: i / 20,
    })),
  });
  hook.mockReturnValue(data as any);
  render(<Dashboard demo={demo} scope={{ key: 'u:s', ready: true }} />);
  expect(within(screen.getByRole('table', { name: '工作流列表' })).getAllByRole('row')).toHaveLength(11);
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  expect(screen.getByText('Genuine workflow 11')).toBeTruthy();
  fireEvent.change(screen.getByRole('textbox', { name: '搜索工作流' }), { target: { value: 'workflow 2' } });
  expect(screen.getByText('Genuine workflow 2')).toBeTruthy();
  expect(screen.queryByText('Genuine workflow 11')).toBeNull();
  expect(screen.queryByRole('button', { name: '下一页' })).toBeNull();
});
