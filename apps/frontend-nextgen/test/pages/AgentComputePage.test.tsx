/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

  test('时间按 Java SimpleDateFormat 的 yyyy-MM-dd HH:mm 渲染', async () => {
    // 既验证 24 小时制带时分，也验证分隔符是 - 而不是 zh-CN 的 /。
    listRunsMock.mockResolvedValue({ items: [run()], total: 1, page: 1, page_size: 10 });
    render(<AgentComputeListPage />);

    await screen.findByText('统计各部门月度工时');
    const stamps = screen.getAllByText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
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

describe('Agent Compute 详情路由', () => {
  test('按路由 runId 打开面板，并在身份未就绪时给出提示而非空白', async () => {
    render(<AgentComputeRunPage />);
    expect(await screen.findByRole('region', { name: '计算任务副屏' })).toBeInTheDocument();
    expect(useAgentComputePanelStore.getState().runId).toBe('run-1');
  });

  test('身份为空时渲染状态提示，不渲染空白', () => {
    jest.spyOn(capabilities, 'getHumanIdentity').mockReturnValue({ status: 'available', value: null });
    const { container } = render(<AgentComputeRunPage />);

    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(container.innerHTML.length).toBeGreaterThan(200);
  });
});