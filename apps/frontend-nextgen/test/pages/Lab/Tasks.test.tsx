/** @jest-environment jsdom */
// 实验室「任务」体验页(PRD:TeamClaw-demo /tasks):跨Bot、跨群协同任务列表,
// 状态类别筛选 + 定制任务表格 + TaskPanel 产物抽屉。与定时任务无关。
import LabTasksPage from '@/pages/Lab/Tasks';
import { beforeEach, describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

// Lab 系页面统一 mock @umijs/max(esbuild Buffer 不变量 + 页内有 history.push 跳对话/协作群)。
// hoisted factory 先于 @jest/globals 绑定求值,须用全局 jest(同 LabLandingPage.test 先例)。
jest.mock('@umijs/max', () => ({
  history: { push: jest.fn(), replace: jest.fn() },
  useLocation: () => ({ pathname: '/lab/tasks' }),
}));

// 数据层:登录人身份 + listMyTasks 返回一条协同任务。
jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({
    identity: { userId: 'u-1', displayName: '测试用户', kind: 'user', id: 'user:u-1', online: true },
    status: 'ready',
  }),
}));
jest.mock('@/pages/Workspace/hooks/useOwnedBots', () => ({
  useOwnedBots: () => ({
    chatBots: [
      { botId: 'b-9', realBotId: 'b-9', displayName: '调研助手' },
      { botId: 'b-10:u-1', realBotId: 'b-10', displayName: '写作助手' },
    ],
    isLoading: false,
  }),
}));
const mockListMyTasks = jest.fn();
const mockNormalizeMyTaskPage = jest.fn((data: unknown) => ({
  items: Array.isArray(data) ? data : [],
  total: Array.isArray(data) ? data.length : 0,
  page: 1,
  pageSize: 10,
}));
jest.mock('@/services/myTask', () => {
  // 其余导出(runtimeStatusesFromProductFilter 等)保真,仅拦截网络相关的 listMyTasks。
  const actual = jest.requireActual('@/services/myTask') as Record<string, unknown>;
  return {
    ...actual,
    listMyTasks: (...args: unknown[]) => mockListMyTasks(...(args as [])),
    normalizeMyTaskPage: (data: unknown) => mockNormalizeMyTaskPage(data),
    isEnvelopeFailure: () => false,
  };
});

const mockPush = jest.fn();

// jsdom 未实现 Pointer Capture，补齐 Radix Select 测试所需的最小 API。
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= () => undefined;
Element.prototype.releasePointerCapture ??= () => undefined;
Element.prototype.scrollIntoView ??= () => undefined;

/** 详情副屏(TaskPanel V2)的 dashboard 信封。 */
const dashboardEnvelope = {
  code: 200000,
  message: 'ok',
  request_id: 'r',
  data: {
    task_id: 't-collab-1',
    run_id: 1,
    status: 'EXECUTING',
    needs_attention: false,
    task_type: 'dynamic',
    source_type: 'coop_group',
    owner_user_id: 'u-1',
    owner_bot_id: 'b-9',
    task_spec: { metadata: { title: '竞品协同分析' }, goal: { objective: '对比三家竞品', acceptances: [] } },
    create_time: '2026-10-21 10:00:00',
    finish_time: null,
    loop_round: 1,
    progress: { total: 1, pending: 0, planning: 0, running: 1, done: 0, failed: 0, hung: 0, skipped: 0, percent: 0 },
    tasks: [
      {
        node_id: 'n1',
        task_id: 't-collab-1',
        sequence: 1,
        status: 'RUNNING',
        task_spec: { metadata: { title: '并行调研' }, goal: { objective: '', acceptances: [] } },
        artifacts: [
          {
            artifact_id: 'artifact-1',
            artifact_name: '分析报告.md',
            kind: 'file',
            content: {
              storage_type: 'session_file',
              storage_id: 'bcs_grp_group-1:1',
              file_id: 'file-1',
              file_name: '分析报告.md',
              file_type: 'text/markdown',
            },
            creator: 'b-9',
            created_at: 1792557600000,
          },
          {
            artifact_id: 'artifact-text',
            artifact_name: '执行摘要',
            kind: 'text',
            content: { text: '纯文本执行摘要' },
            creator: 'b-9',
            created_at: 1792557601000,
          },
          {
            artifact_id: 'artifact-link',
            artifact_name: '需求文档',
            kind: 'url_link',
            content: { url: 'https://docs.example/task-requirement', type: '需求文档' },
            creator: 'group-1',
            created_at: 1792557602000,
          },
          {
            artifact_id: 'artifact-code',
            artifact_name: '实现 PR',
            kind: 'code',
            content: {
              code_base_name: 'avernet',
              code_base_address: 'https://git.example/avernet',
              pr_link: 'https://git.example/avernet/pull/1',
              pr_desc: '实现任务产物预览',
            },
            creator: 'b-9',
            created_at: 1792557603000,
          },
        ],
        run_info: { action_log: [{ seq: 1, ts: 1700000000000, action: 'dispatch', attempt: 1 }] },
      },
    ],
    relations: [],
  },
};

const listItem = {
  id: 1,
  task_id: 't-collab-1',
  source_type: 'coop_group',
  owner_user_id: 'u-1',
  owner_bot_id: 'b-9',
  execution_config: {
    task_type: 'dynamic',
    main_session_id: 'bcs_grp_group-1:1',
    source_group_id: 'group-1',
  },
  task_spec: {
    metadata: { title: '旧标题' },
    context: { title: '竞品协同分析', background: '' },
    goal: { objective: '对比三家竞品', acceptances: [] },
  },
  status: 'EXECUTING',
  gmt_create: '2026-10-21 10:00:00',
  gmt_modified: '2026-10-21 10:00:00',
};

const writingTaskListItem = {
  ...listItem,
  id: 2,
  task_id: 't-writing-2',
  owner_bot_id: 'b-10',
  task_spec: {
    metadata: { title: '旧标题' },
    context: { title: '发布稿撰写', background: '' },
    goal: { objective: '整理发布稿', acceptances: [] },
  },
};

beforeEach(() => {
  jest.clearAllMocks();
  const push = jest.requireMock('@umijs/max').history.push;
  push.mockImplementation?.((...args: unknown[]) => mockPush(...(args as [])));
  mockPush.mockClear();
  mockListMyTasks.mockResolvedValue({ code: 0, message: 'ok', data: [listItem], request_id: 'r' });
  (global as unknown as { fetch: unknown }).fetch = jest.fn((input: unknown) => {
    const url = typeof input === 'string' ? input : '';
    if (url.includes('/api/v1/collaboration/sessions/bcs_grp_group-1%3A1/files/file-1/content')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        blob: async () => new Blob(['# 分析结论\n\n已完成 **三家竞品** 对比。'], { type: 'text/markdown' }),
      });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => dashboardEnvelope });
  });
});

describe('LabTasksPage(实验室·任务体验页)', () => {
  it('页头对齐 PRD:标题「任务」+ 跨Bot跨群协同任务描述 + 对话/协作群入口', async () => {
    render(<LabTasksPage />);
    expect(screen.getByRole('heading', { name: '任务' })).toBeInTheDocument();
    expect(screen.getByText(/查看跨Bot、跨群的协同任务/)).toBeInTheDocument();
    const chatLink = screen.getByRole('button', { name: '对话' });
    const groupLink = screen.getByRole('button', { name: '协作群' });
    fireEvent.click(chatLink);
    fireEvent.click(groupLink);
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/workspace/chat'));
    expect(mockPush).toHaveBeenCalledWith('/workspace/collaboration');
  });

  it('按实验室任务规范展示列、任务 ID、标题、目标和操作', async () => {
    render(<LabTasksPage />);
    await waitFor(() => expect(screen.getByText('竞品协同分析')).toBeInTheDocument());

    expect(screen.getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      '任务',
      '任务目标',
      'Owner Bot',
      '创建时间',
      '完成时间',
      '状态',
      '操作',
    ]);
    expect(screen.getByText('t-collab-1')).toBeInTheDocument();
    expect(screen.getByText('对比三家竞品')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('搜索任务标题')).toBeInTheDocument();
    expect(screen.getByText('全部状态')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Owner Bot' })).toHaveTextContent('全部 Owner Bot');
    expect(screen.getByText('调研助手')).toBeInTheDocument();

    const row = screen.getByText('t-collab-1').closest('tr');
    expect(row).not.toBeNull();
    expect(within(row!).getByText('调研助手')).toBeInTheDocument();
    expect(within(row!).queryByText('协作群')).not.toBeInTheDocument();
    expect(within(row!).queryByText('动态任务')).not.toBeInTheDocument();
    expect(within(row!).getByRole('button', { name: '查看产物' })).toBeInTheDocument();
    expect(within(row!).getByRole('button', { name: '进入对话' })).toBeInTheDocument();
    expect(within(row!).queryByRole('button', { name: '查看详情' })).not.toBeInTheDocument();
  });

  it('支持按 Owner Bot 筛选当前任务列表', async () => {
    mockListMyTasks.mockResolvedValue({
      code: 0,
      message: 'ok',
      data: [listItem, writingTaskListItem],
      request_id: 'r',
    });

    render(<LabTasksPage />);
    await waitFor(() => expect(screen.getByText('发布稿撰写')).toBeInTheDocument());

    const ownerBotSelect = screen.getByRole('combobox', { name: 'Owner Bot' });
    fireEvent.click(ownerBotSelect);
    expect(screen.getByRole('option', { name: '调研助手' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '写作助手' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('option', { name: '调研助手' }));

    await waitFor(() => expect(screen.queryByText('发布稿撰写')).not.toBeInTheDocument());
    expect(screen.getByText('竞品协同分析')).toBeInTheDocument();
    expect(ownerBotSelect).toHaveTextContent('调研助手');
  });

  it('点击查看产物后复用 TaskPanel 的文件、文本预览并保留外链跳转', async () => {
    render(<LabTasksPage />);
    await waitFor(() => expect(screen.getByText('竞品协同分析')).toBeInTheDocument());

    const fetchMock = (global as unknown as { fetch: jest.Mock }).fetch;
    fetchMock.mockClear();
    fireEvent.click(screen.getByRole('button', { name: '查看产物' }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          (call: unknown[]) =>
            typeof call[0] === 'string' &&
            (call[0] as string).includes('/openapi/v1/collaboration/tasks/dashboard') &&
            (call[0] as string).includes('task_id=t-collab-1'),
        ),
      ).toBe(true),
    );
    const taskArtifactsDialog = await screen.findByRole('dialog');
    expect(taskArtifactsDialog).toBeInTheDocument();
    expect(screen.getByText('任务产物')).toBeInTheDocument();
    expect(await screen.findByText('分析报告.md')).toBeInTheDocument();
    const previewButton = screen.getByRole('button', { name: '预览产物 分析报告.md' });
    expect(previewButton).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载产物 分析报告.md' })).toBeInTheDocument();

    fireEvent.click(previewButton);

    const previewDrawer = await screen.findByTestId('task-artifact-preview-drawer');
    expect(taskArtifactsDialog).toContainElement(previewDrawer);
    expect(previewDrawer).toHaveStyle({ position: 'absolute' });
    expect(await screen.findByRole('heading', { name: '分析结论', level: 1 })).toBeInTheDocument();
    const markdown = document.querySelector('.markdown-body');
    expect(markdown).toHaveClass('app-scrollbar', 'overflow-y-auto', 'px-6', 'py-4', 'text-sm', 'leading-6');

    const anchorClick = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    try {
      fireEvent.click(screen.getByRole('button', { name: '下载文件' }));
      expect(anchorClick).toHaveBeenCalledTimes(1);
      expect(screen.getByTestId('task-artifact-preview-drawer')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: '关闭' }));
      expect(screen.queryByTestId('task-artifact-preview-drawer')).not.toBeInTheDocument();
      expect(screen.getByText('任务产物')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: '预览产物 执行摘要' }));
      expect(await screen.findByTestId('task-artifact-plain-text')).toHaveTextContent('纯文本执行摘要');
      fireEvent.click(screen.getByRole('button', { name: '下载文件' }));
      expect(await screen.findByRole('alert')).toHaveTextContent('纯文本类型的产物不支持下载。');

      fireEvent.click(screen.getByRole('button', { name: '关闭' }));

      // 链接和代码产物仅保留外链跳转，不再提供站内预览详情。
      expect(screen.getByRole('link', { name: '跳转产物 需求文档' })).toHaveAttribute(
        'href',
        'https://docs.example/task-requirement',
      );
      expect(screen.getByRole('link', { name: '跳转产物 实现 PR' })).toHaveAttribute(
        'href',
        'https://git.example/avernet/pull/1',
      );
      expect(screen.queryByRole('button', { name: '预览产物 需求文档' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '预览产物 实现 PR' })).not.toBeInTheDocument();
      expect(screen.queryByTestId('task-artifact-link-details')).not.toBeInTheDocument();
      expect(screen.queryByTestId('task-artifact-code-details')).not.toBeInTheDocument();
    } finally {
      anchorClick.mockRestore();
    }
  });

  it('进入对话沿用原查看会话路由逻辑', async () => {
    render(<LabTasksPage />);
    await waitFor(() => expect(screen.getByText('竞品协同分析')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '进入对话' }));

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith(expect.stringContaining('/workspace/collaboration')));
  });
});
