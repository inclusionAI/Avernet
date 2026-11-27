/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { OverviewPage } from '@/pages/TaskEscort/parity/overview/OverviewPage';
import { overviewService } from '@/services/taskEscortParity/overviewService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@/services/taskEscortParity/overviewService', () => ({
  overviewService: {
    runs: jest.fn(),
    health: jest.fn(),
    trend: jest.fn(),
    nodeStats: jest.fn(),
    runDetail: jest.fn(),
    workflow: jest.fn(),
    logs: jest.fn(),
    archive: jest.fn(),
  },
}));
const service = overviewService as jest.Mocked<typeof overviewService>;
const context: TaskEscortParityContext = {
  scopeKey: 'alice',
  userId: 'alice',
  ready: true,
  workflowId: 'wf',
  workflow: { id: 'wf', title: 'Actual workflow', runCount: 101, lastStatus: 'running', ownerId: null },
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const row = {
  id: 'real-run',
  workflowId: 'wf',
  workflowTitle: 'Actual workflow',
  status: 'failed',
  nodeCount: 1,
  succeededCount: 0,
  failedCount: 1,
  startedAt: 1794902400000,
  durationMs: 2000,
  initiator: 'real-user',
  version: 3,
  engine: 'runner',
};
const node = {
  id: 'real-node:2',
  runId: 'real-run',
  nodeId: 'real-node',
  title: 'Real node',
  status: 'failed',
  attempt: 2,
  durationMs: 1000,
  input: '{"actualInput":true}',
  output: 'real output',
  error: 'real error',
  tokens: '{"totalTokens":15}',
  context: null,
  startedAt: 1794902400000,
};
beforeEach(() => {
  jest.clearAllMocks();
  sessionStorage.clear();
  service.runs.mockImplementation(
    async (params) =>
      ({
        runs: params.limit === 1 ? [] : [row],
        total: 101,
        statusCounts: { succeeded: 80, failed: 20, running: 1 },
      } as never),
  );
  service.health.mockResolvedValue({
    overallScore: 81,
    p95DurationMs: 1800,
    bottleneckNode: 'real-node',
    fragileNode: 'real-node',
    recommendation: 'real recommendation',
  } as never);
  service.trend.mockResolvedValue([
    { date: '2026-11-17', totalRuns: 100, succeededRuns: 80, failedRuns: 20, successRate: 80 },
  ]);
  service.nodeStats.mockResolvedValue({
    workflowId: 'wf',
    totalRuns: 101,
    nodes: [
      {
        nodeId: 'real-node',
        totalExecutions: 101,
        failedCount: 20,
        failureRate: 20 / 101,
        p95DurationMs: 1800,
        errorCategories: [],
      },
    ],
  } as never);
  service.runDetail.mockResolvedValue({ run: row, nodes: [node] } as never);
  service.workflow.mockResolvedValue({
    id: 'wf',
    nodes: [{ id: 'real-node', title: 'Real node', dependsOn: [] }],
  } as never);
  service.logs.mockResolvedValue([]);
  service.archive.mockRejectedValue(new Error('档案未提供'));
});

it('matches overview hierarchy, actual counts and server pagination/search/reset', async () => {
  render(<OverviewPage context={context} />);
  await screen.findByRole('button', { name: 'real-run' });
  expect(within(screen.getByRole('region', { name: '工作流关键指标' })).getByText('101')).toBeInTheDocument();
  for (const label of ['健康度', '运行成功率', '异常结束', '节点耗时 P95', '总运行实例', '运行风险'])
    expect(screen.getByText(label)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await waitFor(() =>
    expect(service.runs).toHaveBeenCalledWith(
      expect.objectContaining({ workflowId: 'wf', user_id: 'alice', limit: 20, offset: 20 }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索运行记录' }), { target: { value: 'needle' } });
  fireEvent.click(screen.getByRole('button', { name: '搜索' }));
  await waitFor(() =>
    expect(service.runs).toHaveBeenCalledWith(
      expect.objectContaining({ query: 'needle', offset: 0 }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: '重置筛选' }));
  await waitFor(() => expect(screen.getByRole('searchbox', { name: '搜索运行记录' })).toHaveValue(''));
  fireEvent.click(screen.getByRole('button', { name: '昨天' }));
  await waitFor(() =>
    expect(service.runs).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 1, to: expect.any(Number), from: expect.any(Number) }),
      expect.any(AbortSignal),
    ),
  );
});

it('risk node opens actual node analysis filter and write actions are honestly disabled', async () => {
  render(<OverviewPage context={context} />);
  await screen.findByText('real recommendation');
  fireEvent.click(screen.getByRole('button', { name: /耗时瓶颈/ }));
  await screen.findByRole('button', { name: 'real-node' });
  expect(screen.getByRole('searchbox', { name: '筛选节点' })).toHaveValue('real-node');
  fireEvent.click(screen.getByRole('tab', { name: '运行列表' }));
  expect(screen.getByRole('button', { name: '分析 real-run' })).toBeDisabled();
  expect(screen.getByText(/写入协议与权限确认尚未接入/)).toBeInTheDocument();
});

it('tertiary detail opens with preserved filters, real evidence tabs and restores run focus', async () => {
  const view = render(<OverviewPage context={context} />);
  await screen.findByRole('button', { name: 'real-run' });
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索运行记录' }), { target: { value: 'real' } });
  fireEvent.click(screen.getByRole('button', { name: '搜索' }));
  fireEvent.click(await screen.findByRole('button', { name: 'real-run' }));
  expect(context.navigate).toHaveBeenCalledWith(
    'runs',
    expect.objectContaining({ runId: 'real-run', runQuery: 'real' }),
  );
  view.rerender(<OverviewPage context={{ ...context, query: { runId: 'real-run' } }} />);
  await screen.findByRole('button', { name: 'Real node' });
  for (const name of ['节点', '日志', '运行档案']) expect(screen.getByRole('tab', { name })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Real node' }));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByText('real error')).toBeInTheDocument();
  expect(within(dialog).getByText(/actualInput/)).toBeInTheDocument();
  expect(within(dialog).getByText('接口未提供系统上下文')).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: '关闭节点详情' }));
  fireEvent.click(screen.getByRole('tab', { name: '日志' }));
  await screen.findByText('暂无实际运行日志');
  expect(screen.getByRole('region', { name: '运行 DAG' })).toBeInTheDocument();
  await screen.findByRole('button', { name: /DAG 节点 Real node/ });
  fireEvent.click(screen.getByRole('tab', { name: '运行档案' }));
  await screen.findByText('档案未提供');
  fireEvent.click(screen.getByRole('button', { name: '返回运行列表' }));
  view.rerender(<OverviewPage context={context} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'real-run' })).toHaveFocus());
  expect(screen.getByRole('searchbox', { name: '搜索运行记录' })).toHaveValue('real');
});

it('keeps true empty, 403 error and not-ready states instead of fake data', async () => {
  service.runs.mockResolvedValue({ runs: [], total: 0, statusCounts: null });
  const view = render(<OverviewPage context={context} />);
  await screen.findByText('暂无运行');
  service.runs.mockRejectedValue(new Error('403 无权限'));
  view.rerender(<OverviewPage context={{ ...context, workflowId: 'other' }} />);
  await screen.findAllByText('403 无权限');
  view.rerender(<OverviewPage context={{ ...context, ready: false, workflowId: '' }} />);
  expect(screen.queryByText('real-run')).not.toBeInTheDocument();
  expect(screen.getByText('请选择真实工作流')).toBeInTheDocument();
});

it('synchronizes same-workflow Back/Forward committed filters, but not runId-only transitions', async () => {
  const first = { runQuery: 'first', runStatus: 'failed', runRange: '3d', runPage: '2' };
  const second = { runQuery: 'second', runStatus: 'running', runRange: '1d', runPage: '1' };
  const view = render(<OverviewPage context={{ ...context, query: first }} />);
  for (const query of [first, second, first]) {
    view.rerender(<OverviewPage context={{ ...context, query }} />);
    await waitFor(() => expect(screen.getByRole('searchbox', { name: '搜索运行记录' })).toHaveValue(query.runQuery));
    await screen.findByRole('button', { name: 'real-run' });
    await waitFor(() => {
      const calls = service.runs.mock.calls.filter(([params]) => params.limit === 20);
      const params = calls[calls.length - 1][0];
      expect(params).toEqual(
        expect.objectContaining({ query: query.runQuery, status: query.runStatus, offset: Number(query.runPage) * 20 }),
      );
      expect(params.to! - params.from!).toBe(query.runRange === '3d' ? 3 * 86400 : 86400);
    });
    expect(screen.getByRole('combobox', { name: '运行状态' })).toHaveTextContent(
      query.runStatus === 'failed' ? '失败' : '运行中',
    );
    expect(screen.getByRole('combobox', { name: '运行时间范围' })).toHaveTextContent(
      query.runRange === '3d' ? '近 3 天' : '近 1 天',
    );
  }
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索运行记录' }), { target: { value: 'local search' } });
  fireEvent.click(screen.getByRole('button', { name: '搜索' }));
  await waitFor(() =>
    expect(service.runs).toHaveBeenCalledWith(
      expect.objectContaining({ query: 'local search', offset: 0 }),
      expect.any(AbortSignal),
    ),
  );
  view.rerender(<OverviewPage context={{ ...context, query: { ...first, runId: 'real-run' } }} />);
  await screen.findByRole('button', { name: 'Real node' });
  view.rerender(<OverviewPage context={{ ...context, query: first }} />);
  expect(screen.getByRole('searchbox', { name: '搜索运行记录' })).toHaveValue('local search');
});
