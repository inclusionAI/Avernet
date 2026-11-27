/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { RunDetail } from '@/pages/TaskEscort/parity/overview/RunDetail';
import { mapOverviewDetailedRun, mapOverviewNode } from '@/services/taskEscortParity/overviewMapper';
import { overviewService } from '@/services/taskEscortParity/overviewService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@/services/taskEscortParity/overviewService', () => ({
  overviewService: { runDetail: jest.fn(), workflow: jest.fn(), logs: jest.fn(), archive: jest.fn() },
}));
const service = overviewService as jest.Mocked<typeof overviewService>;
const context: TaskEscortParityContext = {
  scopeKey: 'alice',
  userId: 'alice',
  ready: true,
  workflowId: 'wf',
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const wire = {
  flow_id: 'run-actual',
  workflow_id: 'wf',
  workflow_title: '真实工作流',
  status: 'waiting',
  node_count: 5,
  total_token_usage: 0,
  workflow_version: 3,
  input_json: JSON.stringify({ message: '已记录的用户要求', params: { target: 'later-not-original' } }),
  params_json: JSON.stringify({ target: 'original', long: '业务参数'.repeat(220), apiKey: 'xxxxxxxx' }),
  parent_flow_id: 'parent-only',
  root_flow_id: 'root-only',
  device_id: 'server-device',
  bot_type: 'server-bot',
  stage: 'verify',
  credentials_json: '{"iamToken":"hidden-credential"}',
};
const executions = [
  ['a', 1, 'failed'],
  ['a', 2, 'running'],
  ['b', 1, 'succeeded'],
  ['c', 1, 'failed'],
  ['d', 1, 'waiting'],
  ['e', 1, 'blocked'],
].map(([id, attempt, status]) =>
  mapOverviewNode({
    flow_id: 'run-actual',
    node_id: id,
    node_title: `实际节点 ${id}`,
    attempt,
    status,
    input_json: '{"nodeOnly":"not-launch"}',
    output_json: `实际输出 ${id}`,
    error_text: status === 'failed' ? '真实错误' : null,
  }),
);
beforeEach(() => {
  jest.clearAllMocks();
  service.runDetail.mockResolvedValue({ run: mapOverviewDetailedRun(wire), nodes: executions });
  service.workflow.mockResolvedValue({
    id: 'wf',
    version: '4',
    nodes: executions
      .filter((n) => n.attempt === 1)
      .map((n) => ({
        id: n.nodeId,
        title: n.title!,
        executor: null,
        dependsOn: n.nodeId === 'a' ? [] : ['a'],
      })),
  });
  service.logs.mockResolvedValue([
    {
      id: 'l1',
      timestamp: null,
      relativeMs: null,
      nodeId: 'a',
      level: 'info',
      source: 'runner',
      message: '实际正常日志',
    },
    {
      id: 'l2',
      timestamp: null,
      relativeMs: null,
      nodeId: 'c',
      level: 'error',
      source: 'runner',
      message: '实际错误日志',
    },
  ]);
  service.archive.mockResolvedValue({
    id: 'archive-real',
    status: 'collected',
    createdAt: null,
    errors: [],
    failedNodeCount: 1,
    failedNodes: [],
    rootCauseHints: [],
    analysis: { source: 'archive', status: 'completed', summary: '实际单次分析证据', error: null, diagnoses: [] },
    suggestedYaml: null,
    sections: [
      {
        key: 'flowRun',
        label: '流程信息',
        value: { credentials_json: 'hidden-archive', system_context: 'hidden-system', safe: '实际流程信息' },
      },
    ],
  });
});

it('separates identity/runtime/operations/input/lineage and preserves latest-attempt terminal progress and zero tokens', async () => {
  render(<RunDetail context={context} runId="run-actual" onBack={jest.fn()} />);
  const identity = await screen.findByRole('region', { name: '运行身份' });
  expect(document.body).not.toHaveTextContent(/\bDEMO\b/i);
  const runtime = screen.getByRole('region', { name: '运行上下文' });
  expect(within(identity).getByText('wf')).toBeInTheDocument();
  expect(within(runtime).getByText('server-device')).toBeInTheDocument();
  expect(within(identity).queryByText('server-device')).not.toBeInTheDocument();
  expect(screen.getByRole('region', { name: '运行操作' })).toBeInTheDocument();
  expect(
    within(screen.getByRole('region', { name: '用户启动输入' })).getByText('已记录的用户要求'),
  ).toBeInTheDocument();
  expect(screen.getByText(/不保证原始请求逐字一致/)).toBeInTheDocument();
  const lineage = screen.getByRole('complementary', { name: '运行链路' });
  expect(within(lineage).getByText('parent-only')).toBeInTheDocument();
  expect(within(lineage).getByText('root-only')).toBeInTheDocument();
  expect(screen.getByText('1 成功 / 1 失败 / 5 总计')).toBeInTheDocument();
  expect(screen.getByRole('progressbar', { name: '实际节点进度' })).toHaveAttribute('aria-valuenow', '2');
  expect(screen.getByRole('region', { name: '运行 Token 用量' })).toHaveTextContent('0');
  const expand = screen.getByRole('button', { name: '展开原始启动参数' });
  const content = document.getElementById(expand.getAttribute('aria-controls')!)!;
  expect(expand).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(expand);
  expect(screen.getByRole('button', { name: '收起原始启动参数' })).toHaveAttribute('aria-expanded', 'true');
  expect(content).toHaveTextContent('original');
  expect(content).not.toHaveTextContent('later-not-original');
  expect(document.body).not.toHaveTextContent('xxxxxxxx');
  expect(document.body).not.toHaveTextContent('hidden-credential');
  expect(service.runDetail).toHaveBeenCalledTimes(1);
});

it('places an inspectable DAG above details with legend, working zoom/fit/expand and actual node evidence', async () => {
  render(<RunDetail context={context} runId="run-actual" onBack={jest.fn()} />);
  const dag = await screen.findByRole('region', { name: '运行 DAG' });
  const tabs = screen.getByLabelText('运行详情页签');
  expect(dag.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await screen.findByRole('button', { name: 'DAG 节点 实际节点 a' });
  expect(within(dag).getByLabelText('DAG 状态图例')).toBeInTheDocument();
  expect(within(dag).getByLabelText('DAG 缩放比例')).toHaveTextContent('100%');
  fireEvent.click(within(dag).getByRole('button', { name: '放大 DAG' }));
  expect(within(dag).getByLabelText('DAG 缩放比例')).toHaveTextContent('125%');
  fireEvent.click(within(dag).getByRole('button', { name: '缩小 DAG' }));
  expect(within(dag).getByLabelText('DAG 缩放比例')).toHaveTextContent('100%');
  fireEvent.click(within(dag).getByRole('button', { name: '适应 DAG 画布' }));
  fireEvent.click(within(dag).getByRole('button', { name: '展开 DAG 画布' }));
  expect(within(dag).getByRole('button', { name: '收起 DAG 画布' })).toHaveAttribute('aria-expanded', 'true');
  fireEvent.click(within(dag).getByRole('button', { name: 'DAG 节点 实际节点 c' }));
  const evidence = await screen.findByRole('dialog');
  expect(within(evidence).getByText('真实错误')).toBeInTheDocument();
  expect(within(evidence).getByText('实际输出 c')).toBeInTheDocument();
});

it('shows separate fullwidth conversation, unavailable approval and explicit single-run vs aggregate status without fabricated success', async () => {
  render(<RunDetail context={context} runId="run-actual" onBack={jest.fn()} />);
  const conversation = await screen.findByRole('region', { name: '人工干预对话' });
  const approval = screen.getByRole('region', { name: '运行审批' });
  expect(conversation).not.toContainElement(approval);
  expect(within(conversation).getByRole('textbox', { name: '干预消息' })).toBeDisabled();
  expect(within(conversation).getByText(/未接入已验证的对话读取与发送协议/)).toBeInTheDocument();
  for (const name of ['发送', '重试', '跳过', '修正', '确认'])
    expect(within(conversation).getByRole('button', { name })).toBeDisabled();
  expect(within(approval).getByText(/无法确认实际待审批记录/)).toBeInTheDocument();
  expect(within(approval).getByRole('button', { name: '处理审批' })).toBeDisabled();
  expect(screen.getByRole('region', { name: '单次运行分析' })).toHaveTextContent('接口未提供单次运行分析状态');
  expect(screen.getByRole('region', { name: '聚合分析' })).toHaveTextContent('未接入已验证的聚合分析协议');
  expect(screen.queryByText('发送成功')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看单次分析与档案' }));
  await screen.findByText('实际单次分析证据');
  fireEvent.click(screen.getByText('流程信息', { selector: 'summary' }));
  expect(document.body).not.toHaveTextContent('hidden-archive');
  expect(document.body).not.toHaveTextContent('hidden-system');
  expect(screen.getByText(/实际流程信息/)).toBeInTheDocument();
});

it('keeps log filters, node attempts and archive reachable after the graph', async () => {
  render(<RunDetail context={context} runId="run-actual" onBack={jest.fn()} />);
  await screen.findByRole('button', { name: '实际节点 a' });
  fireEvent.click(screen.getByRole('button', { name: '实际节点 a' }));
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByRole('combobox', { name: '执行尝试' })).toHaveTextContent('第 2 次尝试');
  fireEvent.click(within(dialog).getByRole('button', { name: '关闭节点详情' }));
  fireEvent.click(screen.getByRole('tab', { name: '日志' }));
  await screen.findByText('实际正常日志');
  expect(screen.getByRole('combobox', { name: '日志节点' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.queryByText('实际正常日志')).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索日志' }), { target: { value: '无匹配' } });
  expect(screen.getByText('没有匹配的日志')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '运行档案' }));
  await screen.findByText('实际单次分析证据');
});

it('marks absent launch/runtime/lineage unavailable without guessing from nodes or credentials', async () => {
  service.runDetail.mockResolvedValue({
    run: mapOverviewDetailedRun({
      flow_id: 'run-actual',
      workflow_id: 'wf',
      status: 'running',
      hasCredentials: true,
      credentials_json: '{"CLIENT_ID":"never-device"}',
      input_json: '{"params":{"target":"retry"}}',
    }),
    nodes: executions,
  });
  render(<RunDetail context={context} runId="run-actual" onBack={jest.fn()} />);
  const launch = await screen.findByRole('region', { name: '用户启动输入' });
  expect(within(launch).getByText('接口未提供输入文本')).toBeInTheDocument();
  expect(within(launch).getByText('接口未提供原始启动参数')).toBeInTheDocument();
  expect(screen.getByLabelText('父运行未记录')).toBeInTheDocument();
  expect(screen.getByLabelText('根运行未记录')).toBeInTheDocument();
  expect(
    within(screen.getByRole('region', { name: '运行上下文' })).getByText(/服务端未提供已授权的运行元数据/),
  ).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent('never-device');
  expect(within(launch).queryByText('not-launch')).not.toBeInTheDocument();
  await waitFor(() => expect(service.workflow).toHaveBeenCalledTimes(1));
});
