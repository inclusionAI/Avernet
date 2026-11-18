/** @jest-environment jsdom */
import { Dashboard } from '@/pages/TaskEscort/Dashboard';
import { backendRequest } from '@/services/backendApi/httpClient';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = jest.mocked(backendRequest);
const base = '/openapi/v1/harnessflow/api/dashboard/';
const demo = {
  period: '7d',
  setPeriod: jest.fn(),
  navigate: jest.fn(),
  setWorkflowId: jest.fn(),
  workflowId: 'selected-but-not-a-dashboard-filter',
} as any;
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.scrollIntoView ??= () => undefined;

function payload(path: string) {
  switch (path) {
    case 'overview':
      return {
        workflowCount: 2,
        totalRuns: 1234,
        succeededCount: 875,
        failedCount: 125,
        runningCount: 3,
        terminalCount: 1000,
        nonTerminalCount: 234,
        successRate: 0.875,
        prevSuccessRate: 0.8,
        avgDurationMs: 123000,
        totalTokenUsage: 0,
        statusDistribution: { running: 3, waiting: 2 },
        machineDurationP50: 123000,
        prevMachineDurationP50: 130000,
        evolution: {
          available: false,
          verificationAvailable: false,
          diagnosisCount: 0,
          suggestionCount: 0,
          applicationAttemptCount: 0,
          applicationSucceededCount: 0,
          applicationFailedCount: 0,
          applicationSuccessRate: null,
          appliedUnverifiedCount: null,
          recurrenceDetectedCount: null,
          verifiedCount: null,
        },
      };
    case 'daily-trend':
      return { from: 1, to: 2, dates: [] };
    case 'failure-hotspots':
      return { from: 1, to: 2, hotspots: [] };
    case 'workflow-health':
      return {
        available: true,
        from: 1,
        to: 2,
        workflows: [
          {
            workflowId: 'workflow-low',
            workflowTitle: '真实低成功率流程',
            sceneName: '',
            released: false,
            runCount: 12,
            failedCount: 2,
            completionSuccessRate: 0.7,
            selfHealTriggeredRuns: 0,
            selfHealSuccessRate: null,
            machineDurationP50: 1000,
            avgDurationMs: 1000,
          },
          {
            workflowId: 'workflow-high',
            workflowTitle: '真实已发布流程',
            sceneName: '',
            released: true,
            runCount: 100,
            failedCount: 0,
            completionSuccessRate: 1,
            selfHealTriggeredRuns: 0,
            selfHealSuccessRate: null,
            machineDurationP50: 1000,
            avgDurationMs: 1000,
          },
        ],
      };
    case 'workflow-release-stats':
      return {
        available: true,
        workflows: [
          {
            workflowId: 'workflow-high',
            workflowTitle: '真实已发布流程',
            createdAt: 1,
            released: true,
            deployCount: 2,
            rollbackCount: 0,
            devCycleMs: 1000,
            lastDeployAt: 1794800000,
            firstRunSuccessRate: 0.9,
          },
        ],
      };
    default:
      throw new Error(`Unexpected dashboard read: ${path}`);
  }
}
beforeEach(() => {
  jest.clearAllMocks();
  window.history.replaceState({}, '', '/evolution-factory/task-escort?demo=1&workflowId=ignore');
  request.mockImplementation(async (url) => payload(url.replace(base, '')) as any);
});

it('runs the actual Dashboard → hook → real service → gateway controller with no demo/sample route', async () => {
  const ui = render(<Dashboard demo={demo} scope={{ key: 'identity:space', ready: false }} />);
  expect(request).not.toHaveBeenCalled();
  ui.rerender(<Dashboard demo={demo} scope={{ key: 'identity:space', ready: true }} />);
  expect(await screen.findByText('1,234')).toBeTruthy();
  expect(screen.getByText('87.5%')).toBeTruthy();
  expect(request).toHaveBeenCalledTimes(5);
  request.mock.calls.forEach(([url, options]) => {
    expect(url.startsWith(base)).toBe(true);
    expect(options?.method).toBe('GET');
    expect(options?.params).not.toHaveProperty('workflowId');
    expect(options?.params).not.toHaveProperty('demo');
    expect((options?.params?.to as number) - (options?.params?.from as number)).toBe(604800);
  });
  expect(screen.queryByText(/演示|研发效能|模拟快照/)).toBeNull();
});
it('drives real local time requests, filtering/sorting and workflow navigation through accessible controls', async () => {
  const user = userEvent.setup();
  render(<Dashboard demo={demo} scope={{ key: 'identity:space', ready: true }} />);
  await screen.findByText('1,234');
  const choose = async (label: string, option: string) => {
    await user.click(screen.getByRole('combobox', { name: label }));
    await user.click(await screen.findByRole('option', { name: option }));
  };
  await choose('运行质量趋势时间范围', '昨天');
  await waitFor(() => expect(request).toHaveBeenCalledTimes(6));
  expect(request.mock.calls.at(-1)?.[0]).toBe(`${base}daily-trend`);
  const params = request.mock.calls.at(-1)?.[1]?.params;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  expect(params?.to).toBe(Math.floor(today.getTime() / 1000) - 1);
  await choose('异常热点时间范围', '近30天');
  expect(request.mock.calls.at(-1)?.[0]).toBe(`${base}failure-hotspots`);
  await choose('工作流列表时间范围', '近30天');
  await waitFor(() => expect(request).toHaveBeenCalledTimes(9));
  await choose('工作流排序', '运行数 降序');
  let table = screen.getByRole('table', { name: '工作流列表' });
  expect(within(table).getAllByRole('row')[1].textContent).toContain('真实已发布流程');
  await choose('工作流状态', '已发布');
  table = screen.getByRole('table', { name: '工作流列表' });
  expect(within(table).queryByText('真实低成功率流程')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '查看工作流 真实已发布流程' }));
  expect(demo.navigate).toHaveBeenCalledWith('workbench');
  expect(demo.setWorkflowId).toHaveBeenCalledWith('workflow-high');
});
it('handles a genuine forbidden error at the protocol boundary with no partial data leakage', async () => {
  request.mockImplementation(async (url) => {
    if (url.endsWith('failure-hotspots'))
      throw Object.assign(new Error('只有管理员可以查看全局数据大盘'), { status: 403 });
    return payload(url.replace(base, '')) as any;
  });
  render(<Dashboard demo={demo} scope={{ key: 'identity:space', ready: true }} />);
  expect(await screen.findByText('只有管理员可以查看全局数据大盘')).toBeTruthy();
  expect(screen.queryByText('1,234')).toBeNull();
  expect(screen.queryByRole('table', { name: '工作流列表' })).toBeNull();
});
