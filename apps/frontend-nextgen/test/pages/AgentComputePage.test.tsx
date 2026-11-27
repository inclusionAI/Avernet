/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import AgentComputeListPage from '@/pages/AgentCompute/List';
import AgentComputeRunPage from '@/pages/AgentCompute/Run';
import { useAgentComputePanelStore } from '@/stores/agentComputePanelStore';
import { getCapabilities } from '@/capabilities';

jest.mock('@/services/agentswarm/service', () => ({
  getRun: jest.fn().mockResolvedValue(null),
  getRunDetail: jest.fn().mockResolvedValue(null),
  listRuns: jest.fn(),
  submitRun: jest.fn(),
  cancelRun: jest.fn(),
}));
jest.mock('@/pages/Workspace/hooks/useOwnedBots', () => ({
  useOwnedBots: () => ({
    chatBots: [],
    isLoading: false,
    error: null,
    hasAgentCodingBots: false,
    reload: jest.fn(),
  }),
}));
jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));
jest.mock('@/pages/Workspace/components/Modals/YamlEditor', () => ({
  YamlCodeEditor: () => <textarea data-testid="yaml-code-editor" />,
}));
jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  useParams: () => ({ runId: 'run-1' }),
}));

const listRunsMock = jest.requireMock('@/services/agentswarm/service').listRuns as jest.Mock;
const capabilities = getCapabilities();

// jsdom does not implement scrollIntoView, which Radix Select calls when the
// popup opens to bring the chosen item into view.
beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
});

const run = (overrides: Record<string, unknown> = {}) => ({
  run_id: 'run-1',
  row_id: 42,
  status: 'COMPLETED',
  goal_excerpt: '统计各部门月度工时',
  started_at: 1794365805609,
  ended_at: 1794365900000,
  ...overrides,
});

beforeEach(() => {
  listRunsMock.mockReset();
  useAgentComputePanelStore.getState().reset();
  jest
    .spyOn(capabilities, 'getHumanIdentity')
    .mockReturnValue({ status: 'available', value: { userId: '49912', displayName: 'dev', online: true } });
});

describe('Agent Compute 列表（PRD v1.7 §1：主页面是运行列表）', () => {
  test('有任务时以表格列出目标、状态与任务编号', async () => {
    listRunsMock.mockResolvedValue({ items: [run()], total: 1, page: 1, page_size: 20 });
    render(<AgentComputeListPage />);

    expect(await screen.findByText('统计各部门月度工时')).toBeInTheDocument();
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByText('run-1')).toBeInTheDocument();
    // The row key is shown beside the run id.
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看' })).toBeInTheDocument();
  });

  test('列顺序为 ID → 编号 → 状态 → 目标 → 开始 → 结束 → 操作，表头均为两字', async () => {
    listRunsMock.mockResolvedValue({ items: [run()], total: 1, page: 1, page_size: 20 });
    render(<AgentComputeListPage />);

    const headers = (await screen.findAllByRole('columnheader')).map((cell) => cell.textContent);
    expect(headers).toEqual(['ID', '编号', '状态', '目标', '开始', '结束', '操作']);
  });

  test('状态筛选是下拉框，涵盖全部状态与七个网关状态', async () => {
    listRunsMock.mockResolvedValue({ items: [run()], total: 1, page: 1, page_size: 10 });
    render(<AgentComputeListPage />);
    await screen.findByText('统计各部门月度工时');

    const trigger = screen.getByLabelText('筛选任务状态');
    expect(trigger).toBeInTheDocument();
    // 下拉而不是分段按钮：原来的 Segmented 只有三档，漏掉了五态。
    expect(trigger).not.toHaveAttribute('aria-pressed');
    expect(trigger).toHaveTextContent('全部状态');

    fireEvent.keyDown(trigger, { key: 'Enter' });
    const options = await screen.findAllByRole('option');
    expect(options.map((option) => option.textContent)).toEqual([
      '全部状态',
      '执行中',
      '已完成',
      '已失败',
      '已取消',
      '已超时',
      '已终止',
      '已卡住',
    ]);
  });

  test('时间按 Java SimpleDateFormat 的 yyyy-MM-dd HH:mm:ss 渲染', async () => {
    // 既验证 24 小时制带时分，也验证分隔符是 - 而不是 zh-CN 的 /。
    listRunsMock.mockResolvedValue({ items: [run()], total: 1, page: 1, page_size: 10 });
    render(<AgentComputeListPage />);

    await screen.findByText('统计各部门月度工时');
    const stamps = screen.getAllByText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    expect(stamps.length).toBeGreaterThan(0);
    expect(screen.queryByText(/\d{4}\/\d{2}\/\d{2}/)).toBeNull();
  });

  test('结束时间缺失时渲染占位符，而不是 Invalid Date', async () => {
    listRunsMock.mockResolvedValue({
      items: [run({ ended_at: null })],
      total: 1,
      page: 1,
      page_size: 20,
    });
    render(<AgentComputeListPage />);

    await screen.findByText('统计各部门月度工时');
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText(/Invalid Date/)).toBeNull();
  });

  test('点表头把 sort/order 交给网关，而不是本地重排', async () => {
    listRunsMock.mockResolvedValue({ items: [run()], total: 1, page: 1, page_size: 20 });
    render(<AgentComputeListPage />);

    await screen.findByText('统计各部门月度工时');
    // 默认按开始时间倒序下发。
    expect(listRunsMock).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ sort: 'id', order: 'desc', page: 1, page_size: 10 }),
    );

    listRunsMock.mockClear();
    fireEvent.click(screen.getByRole('button', { name: '按开始排序' }));
    expect(listRunsMock).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ sort: 'started_at', order: 'asc' }),
    );
  });

  test('搜索关键字下发给网关（服务端检索）', async () => {
    listRunsMock.mockResolvedValue({ items: [run()], total: 1, page: 1, page_size: 20 });
    render(<AgentComputeListPage />);
    await screen.findByText('统计各部门月度工时');

    listRunsMock.mockClear();
    fireEvent.change(screen.getByLabelText('搜索计算任务'), { target: { value: '工时' } });
    // 输入停顿 300ms 后才检索，避免逐字请求。
    await waitFor(() =>
      expect(listRunsMock).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({ keyword: '工时' }),
      ),
    );
  });

  test('分页控件按 total 渲染，并请求对应页', async () => {
    listRunsMock.mockResolvedValue({ items: [run()], total: 45, page: 1, page_size: 20 });
    render(<AgentComputeListPage />);

    await screen.findByText('统计各部门月度工时');
    expect(screen.getByRole('button', { name: '第 2 页' })).toBeInTheDocument();
    expect(listRunsMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ page: 1, page_size: 10 }));
  });

  test('计算目标单行截断，摘要放在原生 title 上（不用逐行 Radix Tooltip）', async () => {
    listRunsMock.mockResolvedValue({ items: [run()], total: 1, page: 1, page_size: 20 });
    render(<AgentComputeListPage />);

    const goal = await screen.findByText('统计各部门月度工时');
    expect(goal.className).toContain('truncate');
    expect(goal).toHaveAttribute('title', '统计各部门月度工时');
  });

  test('无任务时显示空状态并引导新建', async () => {
    listRunsMock.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
    render(<AgentComputeListPage />);

    expect(await screen.findByText('暂无计算任务')).toBeInTheDocument();
    // Header action and the empty-state action both offer it.
    expect(screen.getAllByRole('button', { name: '新建计算任务' })).toHaveLength(2);
  });

  test('列表页不再渲染对话副屏', async () => {
    listRunsMock.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
    render(<AgentComputeListPage />);

    await screen.findByText('暂无计算任务');
    expect(screen.queryByRole('region', { name: '计算任务副屏' })).toBeNull();
  });
});

describe('Agent Compute 详情页（PRD v1.7 §7 整页布局）', () => {
  const detail = (overrides: Record<string, unknown> = {}) => ({
    run_id: 'run-1',
    row_id: 42,
    status: 'COMPLETED',
    goal: '统计各部门月度工时',
    provider: 'local',
    started_at: 1794365805609,
    ended_at: 1794365900000,
    final_answer: '已完成：共 12 个部门',
    agent_catalog: [{ name: 'w0', role: '统计', kind: 'llm' }],
    agents: [
      {
        agent_id: 'a1',
        name: 'w0',
        role: '统计',
        kind: 'llm',
        status: 'COMPLETED',
        depth: 0,
        spawn_round: 0,
        idempotency_key: 'k1',
        attempts: 1,
        output: 'hello',
      },
      {
        agent_id: 'a2',
        name: 'w1',
        role: '校对',
        kind: 'llm',
        status: 'FAILED',
        depth: 0,
        spawn_round: 0,
        idempotency_key: 'k2',
        attempts: 2,
        error: 'boom',
      },
    ],
    rounds: [
      { round_id: 'r1', stage_index: 0, orchestrator_steps: 3, slowest_attempt_steps: 2,
        cohort_agent_ids: ['a1', 'a2'], started_at: 1794365805609 },
    ],
    guards: [],
    ...overrides,
  });

  beforeEach(() => {
    jest.clearAllMocks();
    const svc = jest.requireMock('@/services/agentswarm/service');
    (svc.getRunDetail as jest.Mock).mockResolvedValue(detail());
  });

  test('提交时的 Agent 配置在执行详情里可读', async () => {
    // The catalog is what was submitted — name, role, kind, model and whether it
    // came from the library. Without it a run cannot be audited once the
    // definition behind it has been revised.
    const svc = jest.requireMock('@/services/agentswarm/service');
    (svc.getRunDetail as jest.Mock).mockResolvedValue(detail({
      agent_catalog: [
        { name: 'w0', role: '统计', kind: 'bot', model: 'provider/Kimi-K2.6',
          definition_id: 'def-1', definition_row_id: 7 },
        { name: 'w1', role: '校对', kind: 'llm', model: null, definition_id: null },
      ],
    }));

    render(<AgentComputeRunPage />);

    expect(await screen.findByText('Agent 配置（2）')).toBeInTheDocument();
    // Scoped to the submitted-config section: the spawned agents below repeat
    // these names, and only the catalog says what was asked for.
    const catalog = screen.getByLabelText('提交时的 Agent 配置');
    expect(within(catalog).getByText('w0')).toBeInTheDocument();
    expect(within(catalog).getByText('统计')).toBeInTheDocument();
    expect(within(catalog).getByText('provider/Kimi-K2.6')).toBeInTheDocument();
    // A submission from the library and one created blank are different origins,
    // and the run has to say which it was.
    expect(within(catalog).getByText('定义库')).toBeInTheDocument();
    expect(within(catalog).getByText('空白创建')).toBeInTheDocument();
  });

  test('阶段产出同时给出指令与输出', async () => {
    const svc = jest.requireMock('@/services/agentswarm/service');
    (svc.getRunDetail as jest.Mock).mockResolvedValue(detail({
      agent_catalog: [{ name: 'w0', role: '统计', kind: 'llm' }],
      agents: [{
        agent_id: 'a1', name: 'w0_1', catalog_name: 'w0', role: '统计',
        kind: 'llm', status: 'COMPLETED', depth: 0, spawn_round: 0,
        idempotency_key: 'k1', attempts: 1,
        objective: '统计各部门的月度工时', output: { text: '共 12 个部门' },
      }],
    }));

    render(<AgentComputeRunPage />);

    // What was asked and what came back, both readable from the detail pane.
    expect(await screen.findByRole('button', { name: /^指令/ })).toBeInTheDocument();
    const output = screen.getByRole('button', { name: /^输出/ });
    fireEvent.click(output);
    await waitFor(() => expect(document.body.textContent).toContain('共 12 个部门'));
  });

  test('已判定完成但结果为空的 Agent 会被明确标注', async () => {
    // The gateway reports COMPLETED for an empty payload; without this the run
    // reads as all-green while one stage produced nothing.
    const svc = jest.requireMock('@/services/agentswarm/service');
    (svc.getRunDetail as jest.Mock).mockResolvedValue(detail({
      agent_catalog: [{ name: 'w0', role: '统计', kind: 'llm' }],
      agents: [{
        agent_id: 'a1', name: 'w0_64fd0b1a337e', catalog_name: 'w0', role: '统计',
        kind: 'llm', status: 'COMPLETED', depth: 0, spawn_round: 0,
        idempotency_key: 'k1', attempts: 1, output: { text: '' },
      }],
    }));

    render(<AgentComputeRunPage />);

    expect(await screen.findByText('结果为空，未满足要求')).toBeInTheDocument();
    expect(screen.getByText(/该 Agent 已判定完成，但产出为空/)).toBeInTheDocument();
  });

  test('同一配置被派发两次时按轮次分组', async () => {
    const svc = jest.requireMock('@/services/agentswarm/service');
    const agent = (id: string, round: number, output: unknown) => ({
      agent_id: id, name: `writer_${id}`, catalog_name: 'writer', role: '写手',
      kind: 'llm', status: 'COMPLETED', depth: 0, spawn_round: round,
      idempotency_key: `k-${id}`, attempts: 1, output,
    });
    (svc.getRunDetail as jest.Mock).mockResolvedValue(detail({
      agent_catalog: [{ name: 'writer', role: '写手', kind: 'llm' }],
      agents: [agent('a1', 0, { text: '' }), agent('a2', 1, { text: 'brave' })],
    }));

    render(<AgentComputeRunPage />);

    // Two rows share one catalog name; the round is what tells them apart.
    // Scoped to the list: the progress column names rounds too.
    const list = await screen.findByLabelText('执行 Agent 列表');
    expect(within(list).getByText('第 1 轮')).toBeInTheDocument();
    expect(within(list).getByText('第 2 轮')).toBeInTheDocument();
    // And the catalog card explains the re-dispatch it caused.
    expect(screen.getByText(/已派发 2 次/)).toBeInTheDocument();
    expect(screen.getByText(/其中 1 次结果为空/)).toBeInTheDocument();
  });

  test('一个 Agent 显示它来自哪条提交配置', async () => {
    const svc = jest.requireMock('@/services/agentswarm/service');
    (svc.getRunDetail as jest.Mock).mockResolvedValue(detail({
      agents: [{
        agent_id: 'a1', name: 'w0_abcdef123456', catalog_name: 'w0', role: '统计',
        kind: 'bot', status: 'COMPLETED', depth: 0, spawn_round: 0,
        idempotency_key: 'k1', attempts: 1, output: 'hello',
      }],
    }));

    render(<AgentComputeRunPage />);

    expect(await screen.findByText('来自 w0')).toBeInTheDocument();
  });

  test('切换执行详情与运行记录时会重新拉取详情', async () => {
    // Each tab shows a different projection of the same growing payload, so a
    // switch is a request for current data, not the page-open snapshot.
    render(<AgentComputeRunPage />);
    const getDetail = jest.requireMock('@/services/agentswarm/service').getRunDetail as jest.Mock;
    await screen.findByText(/执行 2 · 完成 1/);
    const opened = getDetail.mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: '运行记录' }));
    await waitFor(() => expect(getDetail.mock.calls.length).toBe(opened + 1));

    fireEvent.click(screen.getByRole('button', { name: /执行详情/ }));
    await waitFor(() => expect(getDetail.mock.calls.length).toBe(opened + 2));
  });

  test('未完成的运行会按间隔轮询，完成后停止', async () => {
    jest.useFakeTimers();
    try {
      const svc = jest.requireMock('@/services/agentswarm/service');
      (svc.getRun as jest.Mock).mockResolvedValue({
        run_id: 'run-1', status: 'RUNNING', goal_excerpt: 'g', started_at: 1794365805609, ended_at: null,
      });
      (svc.getRunDetail as jest.Mock).mockResolvedValue(detail({ status: 'RUNNING', ended_at: null }));

      render(<AgentComputeRunPage />);
      await act(async () => { await Promise.resolve(); });
      const polls = (svc.getRun as jest.Mock).mock.calls.length;
      expect(polls).toBeGreaterThan(0);

      // One interval later the status is read again rather than going stale.
      await act(async () => {
        jest.advanceTimersByTime(4000);
        await Promise.resolve();
      });
      expect((svc.getRun as jest.Mock).mock.calls.length).toBeGreaterThan(polls);
    } finally {
      jest.useRealTimers();
    }
  });

  test('已完成的运行不再轮询', async () => {
    jest.useFakeTimers();
    try {
      const svc = jest.requireMock('@/services/agentswarm/service');
      (svc.getRun as jest.Mock).mockResolvedValue({
        run_id: 'run-1', status: 'COMPLETED', goal_excerpt: 'g', started_at: 1794365805609, ended_at: 1794365900000,
      });
      (svc.getRunDetail as jest.Mock).mockResolvedValue(detail());

      render(<AgentComputeRunPage />);
      await act(async () => { await Promise.resolve(); });
      const once = (svc.getRun as jest.Mock).mock.calls.length;
      await act(async () => {
        jest.advanceTimersByTime(12000);
        await Promise.resolve();
      });
      // A finished run costs nothing: no further reads after the first.
      expect((svc.getRun as jest.Mock).mock.calls.length).toBe(once);
    } finally {
      jest.useRealTimers();
    }
  });

  test('状态条展示状态、执行/完成数与起止时间', async () => {
    render(<AgentComputeRunPage />);

    expect(await screen.findByText(/执行 2 · 完成 1 · 进行中 0/)).toBeInTheDocument();
    // 「已完成」既出现在运行状态条，也出现在 Agent 徽章上，所以限定在状态条内查。
    const statusBar = screen.getByRole('region', { name: '运行状态' });
    expect(within(statusBar).getByText('已完成')).toBeInTheDocument();
    // yyyy-MM-dd HH:mm:ss，不是只有日期；状态条里带「开始/结束」前缀。
    expect(within(statusBar).getByText(/开始 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/)).toBeInTheDocument();
    expect(within(statusBar).getByText(/结束 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/)).toBeInTheDocument();
  });

  test('最终结果优先展示', async () => {
    render(<AgentComputeRunPage />);
    expect(await screen.findByText('已完成：共 12 个部门')).toBeInTheDocument();
  });

  test('左侧 Agent 列表可切换，右侧显示所选 Agent 的阶段产出', async () => {
    render(<AgentComputeRunPage />);

    // 默认选第一个
    expect(await screen.findByText('hello')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /w1/ }));
    expect(await screen.findByText('boom')).toBeInTheDocument();
  });

  test('运行记录 Tab 展示编排轮次', async () => {
    render(<AgentComputeRunPage />);
    await screen.findByText(/执行 2 · 完成 1/);

    fireEvent.click(screen.getByRole('button', { name: '运行记录' }));
    expect(await screen.findByText(/第 1 轮编排/)).toBeInTheDocument();
  });

  test('已结束的运行，即使轮次没有结束时间也不显示「进行中」', async () => {
    // round.ended_at 曾经恒为 NULL（后端从不写），把 NULL 读成进行中会把
    // 已完成的运行标成进行中——这正是用户看到的缺陷。
    const svc = jest.requireMock('@/services/agentswarm/service');
    (svc.getRunDetail as jest.Mock).mockResolvedValue(
      detail({ rounds: [
        { round_id: 'r1', stage_index: 0, orchestrator_steps: 3, slowest_attempt_steps: 2,
          cohort_agent_ids: ['a1'], started_at: 1794365805609, ended_at: null },
      ] }),
    );
    render(<AgentComputeRunPage />);
    await screen.findByText(/执行 2 · 完成 1/);

    fireEvent.click(screen.getByRole('button', { name: '运行记录' }));
    const row = await screen.findByText(/第 1 轮编排开始/);
    expect(row.closest('li')?.textContent).not.toContain('进行中');
  });

  test('运行中且轮次未结束，仍显示「进行中」', async () => {
    const svc = jest.requireMock('@/services/agentswarm/service');
    (svc.getRunDetail as jest.Mock).mockResolvedValue(
      detail({ status: 'RUNNING', ended_at: null, rounds: [
        { round_id: 'r1', stage_index: 0, orchestrator_steps: 3, slowest_attempt_steps: 2,
          cohort_agent_ids: ['a1'], started_at: 1794365805609, ended_at: null },
      ] }),
    );
    render(<AgentComputeRunPage />);
    await screen.findByText(/执行 2 · 完成 1/);

    fireEvent.click(screen.getByRole('button', { name: '运行记录' }));
    const row = await screen.findByText(/第 1 轮编排开始/);
    // 「进行中」在时间线条目的详情列，不在标签本身。
    expect(row.closest('li')?.textContent).toContain('进行中');
  });

  test('运行记录：首次尝试显示「第 1 次」，不因下标偏移跳成第 2 次', async () => {
    // slice_retry_index 在数据里是 1-based（三次尝试为 1/2/3），早先多加 1
    // 会把每一次首跑都标成「第 2 次尝试」。
    const svc = jest.requireMock('@/services/agentswarm/service');
    (svc.getRunDetail as jest.Mock).mockResolvedValue(
      detail({
        agents: [
          {
            agent_id: 'a1', name: 'probe_a', role: 'says alpha', kind: 'llm',
            status: 'COMPLETED', depth: 0, spawn_round: 0,
            idempotency_key: 'k1', attempts: 1, output: 'alpha',
            attempt_ledger: [
              { claim_epoch: 1, slice_retry_index: 1, executor_kind: 'llm',
                executor_id: 'e1', executor_name: 'probe_a_run1',
                binding_state: 'bound', result_status: 'COMPLETED',
                started_at: 1794365805609 },
            ],
          },
        ],
      }),
    );
    render(<AgentComputeRunPage />);
    await screen.findByText(/执行 1 · 完成 1/);

    fireEvent.click(screen.getByRole('button', { name: '运行记录' }));
    const row = await screen.findByText(/probe_a 第 1 次尝试/);
    expect(row.textContent).not.toContain('第 2 次');
  });

  test('运行记录：每次尝试都带上该 Agent 的产出', async () => {
    // "COMPLETED" alone cannot explain a run — the empty result that forced a
    // re-dispatch looked exactly like the one that worked.
    const svc = jest.requireMock('@/services/agentswarm/service');
    const attempt = (name: string, output: unknown, startedAt: number) => ({
      agent_id: `a-${name}`, name, catalog_name: 'writer', role: '写手',
      kind: 'llm', status: 'COMPLETED', depth: 0, spawn_round: 0,
      idempotency_key: `k-${name}`, attempts: 1, output,
      attempt_ledger: [{
        claim_epoch: 1, slice_retry_index: 1, executor_kind: 'llm',
        executor_id: `e-${name}`, executor_name: name,
        binding_state: 'bound', result_status: 'COMPLETED', started_at: startedAt,
      }],
    });
    (svc.getRunDetail as jest.Mock).mockResolvedValue(detail({
      agent_catalog: [{ name: 'writer', role: '写手', kind: 'llm' }],
      agents: [attempt('writer_a', { text: '' }, 1794365805609),
               attempt('writer_b', { text: 'brave' }, 1794365810000)],
    }));

    render(<AgentComputeRunPage />);
    await screen.findByText(/执行 2 · 完成 2/);
    fireEvent.click(screen.getByRole('button', { name: '运行记录' }));

    const log = await screen.findByLabelText('运行记录');
    // Outputs start collapsed; opening one shows what the agent produced.
    const outputToggles = within(log).getAllByRole('button', { name: /^输出/ });
    expect(outputToggles).toHaveLength(2);
    fireEvent.click(outputToggles[1]!);
    await waitFor(() => expect(log.textContent).toContain('brave'));
    // And the empty one says so rather than looking like a clean success.
    expect(within(log).getByText('结果为空')).toBeInTheDocument();
  });

  test('运行记录：同一时刻轮次排在该轮尝试之前', async () => {
    const svc = jest.requireMock('@/services/agentswarm/service');
    // 同一毫秒：轮次行比首条尝试晚写，但时间线上它要先开这一轮。
    (svc.getRunDetail as jest.Mock).mockResolvedValue(
      detail({
        rounds: [
          { round_id: 'r1', stage_index: 0, orchestrator_steps: 3,
            slowest_attempt_steps: 2, cohort_agent_ids: ['a1'],
            started_at: 1794365805609, ended_at: null },
        ],
        agents: [
          {
            agent_id: 'a1', name: 'probe_a', role: 'says alpha', kind: 'llm',
            status: 'COMPLETED', depth: 0, spawn_round: 0,
            idempotency_key: 'k1', attempts: 1, output: 'alpha',
            attempt_ledger: [
              { claim_epoch: 1, slice_retry_index: 1, executor_kind: 'llm',
                executor_id: 'e1', executor_name: 'probe_a_run1',
                binding_state: 'bound', result_status: 'COMPLETED',
                started_at: 1794365805609 },
            ],
          },
        ],
      }),
    );
    render(<AgentComputeRunPage />);
    await screen.findByText(/执行 1 · 完成 1/);

    fireEvent.click(screen.getByRole('button', { name: '运行记录' }));
    const items = await screen.findAllByRole('listitem');
    // 同一毫秒按类型排：轮次开这一轮，尝试跟在后面。
    expect(items[0]?.textContent).toContain('第 1 轮编排开始');
  });

  test('整页布局，不再渲染对话副屏', async () => {
    render(<AgentComputeRunPage />);
    await screen.findByText(/执行 2 · 完成 1/);
    expect(screen.queryByRole('region', { name: '计算任务副屏' })).toBeNull();
  });

  test('找不到运行时给出提示而不是空白', async () => {
    const svc = jest.requireMock('@/services/agentswarm/service');
    (svc.getRunDetail as jest.Mock).mockResolvedValue(null);
    render(<AgentComputeRunPage />);

    expect(await screen.findByText('没有找到这次运行')).toBeInTheDocument();
  });
});
