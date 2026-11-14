/** @jest-environment jsdom */
import type { TodoArtifact, TodoEvent, TodoRecord } from '@/domain/todos/models';
import TodoDetailPage from '@/pages/Lab/Todos/TodoDetailPage';
import * as todoService from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn(), replace: jest.fn() },
  useParams: () => ({ todoId: 'todo-1' }),
}));

jest.mock('@/hooks/useSpaceContext', () => ({
  useSpaceContext: (selector: (value: unknown) => unknown) =>
    selector({
      currentSpace: { spaceId: 10, spaceName: '测试空间', spaceType: 'TEAM' },
      currentSpaceId: 10,
      initialized: true,
    }),
}));

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => ({
    identity: { userId: 'user-1', displayName: '风太', avatarUrl: undefined, online: true },
    status: 'ready',
  }),
}));

jest.mock('@/services/todos/todoService', () => ({
  loadTodo: jest.fn(),
  acceptTodo: jest.fn(),
  loadTodoInteractions: jest.fn(),
  addTodoInteraction: jest.fn(),
  loadTodoEvents: jest.fn(),
  loadTodoArtifacts: jest.fn(),
  downloadTodoArtifactContent: jest.fn(),
  startTodoRun: jest.fn(),
}));

jest.mock('@/components/ui/notify', () => ({ notifySuccess: jest.fn() }));
jest.mock('@/utils/errorToast', () => ({ safeReportError: jest.fn() }));

const mockedService = jest.mocked(todoService);
const mockedHistory = jest.mocked(history);

const todo: TodoRecord = {
  todoId: 'todo-1',
  spaceId: 10,
  title: 'Q3 竞品分析',
  description: '整理三款产品的定价与功能矩阵',
  botId: 'bot-1',
  botName: '数据分析助手',
  status: 'pending_acceptance',
  collabContextId: 'ctx-1',
  createdAt: '2026-11-08T01:12:00Z',
  updatedAt: '2026-11-08T06:48:00Z',
};

const artifacts: TodoArtifact[] = [
  {
    artifactId: 'artifact-text',
    todoId: 'todo-1',
    executionId: 'execution-1',
    artifactName: '执行摘要',
    kind: 'text',
    content: { text: '已完成三款产品的定价对比。', isFinalArtifact: false },
    createdAt: '2026-11-08T06:00:00Z',
  },
  {
    artifactId: 'artifact-file',
    todoId: 'todo-1',
    executionId: 'execution-1',
    artifactName: '定价与功能矩阵',
    kind: 'file',
    content: {
      fileName: '定价与功能矩阵.xlsx',
      fileType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      size: 2048,
      isFinalArtifact: true,
    },
    createdAt: '2026-11-08T06:10:00Z',
  },
  {
    artifactId: 'artifact-link',
    todoId: 'todo-1',
    executionId: 'execution-1',
    artifactName: '产品官网',
    kind: 'url_link',
    content: { url: 'https://example.test/product', type: '官网', isFinalArtifact: false },
    createdAt: '2026-11-08T06:20:00Z',
  },
  {
    artifactId: 'artifact-code',
    todoId: 'todo-1',
    executionId: 'execution-1',
    artifactName: '分析脚本',
    kind: 'code',
    content: {
      codeBaseName: 'pricing-analysis',
      codeBaseAddress: 'https://example.test/repo',
      prLink: 'https://example.test/repo/pull/1',
      prDesc: '新增定价采集和对比脚本',
      isFinalArtifact: true,
    },
    createdAt: '2026-11-08T06:30:00Z',
  },
];

function event(eventSeq: number, overrides: Partial<TodoEvent> = {}): TodoEvent {
  return {
    eventId: `event-${eventSeq}`,
    eventSeq,
    eventType: 'execution.status',
    actorId: 'bot-1',
    actorType: 'bot',
    content: `事件 ${eventSeq}`,
    executionId: 'execution-1',
    contextVersion: 1,
    status: 'in_progress',
    createdAt: `2026-11-08T0${eventSeq}:12:00Z`,
    artifacts: [],
    ...overrides,
  };
}

function resetStore() {
  useTodoStore.setState({
    todos: [],
    total: 0,
    selectedTodo: null,
    interactions: [],
    events: [],
    artifacts: [],
    listLoading: false,
    detailLoading: false,
    eventsLoading: false,
    artifactsLoading: false,
    mutationLoading: false,
    listError: null,
    detailError: null,
    eventsError: null,
    artifactsError: null,
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  resetStore();
  mockedService.loadTodo.mockResolvedValue(todo);
  mockedService.loadTodoInteractions.mockResolvedValue([
    event(1, { eventType: 'task.created', actorType: 'human', content: '整理三款产品的定价与功能矩阵' }),
    event(3, {
      eventType: 'task.completed',
      actorType: 'bot',
      content: '三款产品的定价与功能矩阵已完成整理，数据来源已补充。',
    }),
    event(4, { eventType: 'user_interaction.posted', actorType: 'human', content: '请补充价格来源' }),
  ]);
  mockedService.loadTodoEvents.mockResolvedValue([
    event(1, {
      eventType: 'task.created',
      actorType: 'human',
      content: '整理三款产品的定价与功能矩阵',
    }),
    event(2, { eventType: 'task.started', content: '开始采集竞品资料' }),
    event(3, {
      eventType: 'task.completed',
      content: '三款产品的定价与功能矩阵已完成整理，数据来源已补充。',
      status: 'pending_acceptance',
      artifacts: [{ artifactId: 'event-artifact-1', name: '事件产物', url: 'https://example.test/event-artifact' }],
    }),
    event(4, { eventType: 'user_interaction.posted', actorType: 'human', content: '请补充价格来源' }),
  ]);
  mockedService.loadTodoArtifacts.mockResolvedValue(artifacts);
  mockedService.downloadTodoArtifactContent.mockResolvedValue(new Blob(['file']));
  mockedService.addTodoInteraction.mockResolvedValue(event(5, { content: '补充价格来源' }));
  mockedService.acceptTodo.mockResolvedValue({ ...todo, status: 'completed' });
});

describe('TodoDetailPage', () => {
  it('以独立页面展示事件、互动和产物视图', async () => {
    render(<TodoDetailPage />);

    expect(await screen.findByRole('heading', { name: 'Q3 竞品分析' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回待办列表' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '待办详情视图' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '事件' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '互动' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '产物 4' })).toBeInTheDocument();
    const eventView = await screen.findByRole('region', { name: '待办事件' });
    expect(within(eventView).getByText('发起待办：Q3 竞品分析')).toBeInTheDocument();
    expect(within(eventView).getByText('整理三款产品的定价与功能矩阵')).toBeInTheDocument();
    expect(within(eventView).getByText('输出：文本结果')).toBeInTheDocument();
    expect(
      within(eventView).queryByText('三款产品的定价与功能矩阵已完成整理，数据来源已补充。'),
    ).not.toBeInTheDocument();
    expect(within(eventView).getByText('补充要求：请补充价格来源')).toBeInTheDocument();
    expect(within(eventView).queryByText('开始采集竞品资料')).not.toBeInTheDocument();
    expect(within(eventView).queryByText('等待中')).not.toBeInTheDocument();
    expect(mockedService.loadTodoArtifacts).toHaveBeenCalledWith('todo-1');
  });

  it('事件页仅展示 Human 互动原文预览，互动页保留完整内容', async () => {
    const longMessage =
      '请补充三款产品的价格来源、更新时间、企业版与个人版差异，并逐项核对官网、帮助中心、销售页面和公开报价材料，同时标注无法确认的信息、判断依据、风险说明以及后续核验建议。';
    const preview = `${Array.from(longMessage).slice(0, 20).join('')}…`;
    mockedService.loadTodoEvents.mockResolvedValue([
      event(1, {
        eventType: 'task.created',
        actorType: 'human',
        content: '整理三款产品的定价与功能矩阵',
      }),
      event(2, { eventType: 'task.started', content: '开始采集竞品资料' }),
      event(3, { eventType: 'user_interaction.posted', actorType: 'human', content: longMessage }),
    ]);
    mockedService.loadTodoInteractions.mockResolvedValue([
      event(1, {
        eventType: 'task.created',
        actorType: 'human',
        content: '整理三款产品的定价与功能矩阵',
      }),
      event(3, { eventType: 'user_interaction.posted', actorType: 'human', content: longMessage }),
    ]);

    render(<TodoDetailPage />);

    const eventView = await screen.findByRole('region', { name: '待办事件' });
    expect(within(eventView).getByText(`补充要求：${preview}`)).toBeInTheDocument();
    expect(within(eventView).queryByText(longMessage)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: '互动' }));
    expect(within(screen.getByRole('region', { name: '待办互动' })).getByText(longMessage)).toBeInTheDocument();
  });

  it('切换互动视图并提交互动', async () => {
    render(<TodoDetailPage />);
    await screen.findByText('发起待办：Q3 竞品分析');

    fireEvent.click(screen.getByRole('tab', { name: '互动' }));
    const interactionView = screen.getByRole('region', { name: '待办互动' });
    expect(within(interactionView).getAllByText('数据分析助手').length).toBeGreaterThan(0);
    expect(within(interactionView).getAllByText('风太').length).toBeGreaterThan(0);
    expect(within(interactionView).getByText('数据分析助手 开始处理')).toBeInTheDocument();
    expect(within(interactionView).getByText('转为待处理，等待发起人验收')).toBeInTheDocument();
    expect(
      within(interactionView).getByText('三款产品的定价与功能矩阵已完成整理，数据来源已补充。'),
    ).toBeInTheDocument();
    expect(within(interactionView).getByText('定价与功能矩阵.xlsx')).toBeInTheDocument();
    expect(within(interactionView).getByText('产品官网')).toBeInTheDocument();
    expect(await within(interactionView).findByText('请补充价格来源')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('告诉 Bot 需要补充或修改的内容'), {
      target: { value: ' 补充价格来源 ' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送互动' }));

    await waitFor(() =>
      expect(mockedService.addTodoInteraction).toHaveBeenCalledWith('todo-1', { message: '补充价格来源' }),
    );
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();
  });

  it('事件视图内完成验收，拒绝时不调用 Run', async () => {
    mockedService.acceptTodo.mockResolvedValue(todo);
    render(<TodoDetailPage />);
    await screen.findByText('发起待办：Q3 竞品分析');

    const acceptance = screen.getByRole('region', { name: '待办验收' });
    fireEvent.click(within(acceptance).getByRole('button', { name: '验收不通过' }));
    fireEvent.change(within(acceptance).getByPlaceholderText('填写需要 Bot 补充或修改的内容'), {
      target: { value: ' 请修改 ' },
    });
    fireEvent.click(within(acceptance).getByRole('button', { name: '提交问题' }));

    await waitFor(() =>
      expect(mockedService.acceptTodo).toHaveBeenCalledWith('todo-1', {
        verdict: 'rejected',
        message: '请修改',
      }),
    );
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();
  });

  it('产物视图使用独立产物接口展示四类产物，并支持文件下载', async () => {
    const createObjectURL = jest.fn(() => 'blob:todo-artifact');
    const revokeObjectURL = jest.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
    const clickSpy = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    render(<TodoDetailPage />);
    await screen.findByText('发起待办：Q3 竞品分析');

    fireEvent.click(screen.getByRole('tab', { name: '产物 4' }));
    const artifactView = screen.getByRole('region', { name: '待办产物' });
    expect(within(artifactView).getByText('已完成三款产品的定价对比。')).toBeInTheDocument();
    expect(within(artifactView).getByRole('link', { name: /https:\/\/example\.test\/product/ })).toHaveAttribute(
      'href',
      'https://example.test/product',
    );
    expect(within(artifactView).getByRole('link', { name: /pricing-analysis/ })).toHaveAttribute(
      'href',
      'https://example.test/repo',
    );
    expect(within(artifactView).queryByText('事件产物')).not.toBeInTheDocument();

    fireEvent.click(within(artifactView).getByRole('button', { name: '下载' }));
    await waitFor(() =>
      expect(mockedService.downloadTodoArtifactContent).toHaveBeenCalledWith(
        'todo-1',
        'artifact-file',
        'attachment',
      ),
    );
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(clickSpy).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:todo-artifact');
    expect(within(artifactView).queryByText('产物文件读取失败')).not.toBeInTheDocument();
    clickSpy.mockRestore();
  });

  it('返回列表', async () => {
    render(<TodoDetailPage />);
    fireEvent.click(await screen.findByRole('button', { name: '返回待办列表' }));
    expect(mockedHistory.push).toHaveBeenCalledWith('/lab/todos');
  });
  it('使用紧凑的业务事件时间线和验收操作', async () => {
    render(<TodoDetailPage />);
    const eventView = await screen.findByRole('region', { name: '待办事件' });

    const eventName = within(eventView).getByText('发起待办：Q3 竞品分析');
    const eventArtifact = within(eventView).getByText('事件产物');
    const firstEvent = eventName.closest('li');
    const acceptanceHeading = screen.getByRole('heading', { name: '待你验收 · Bot 已完成处理' });
    const acceptance = screen.getByRole('region', { name: '待办验收' });
    const rejectButton = within(acceptance).getByRole('button', { name: '验收不通过' });

    expect(eventName).toHaveClass('text-sm', 'font-medium');
    expect(firstEvent).toHaveClass('pb-4');
    expect(within(eventView).queryByText('数据分析助手')).not.toBeInTheDocument();
    expect(within(eventView).queryByText('开始采集竞品资料')).not.toBeInTheDocument();
    expect(eventArtifact).toHaveClass('text-sm', 'font-medium');
    expect(within(eventView).getByText('外部链接')).toHaveClass('text-xs');
    expect(acceptanceHeading).toHaveClass('text-sm', 'font-semibold');
    expect(rejectButton).toHaveClass('h-8', 'rounded-lg', 'px-4', 'text-xs');
  });

  it('非待验收待办不显示验收操作', async () => {
    mockedService.loadTodo.mockResolvedValue({ ...todo, status: 'completed' });
    render(<TodoDetailPage />);
    await screen.findByText('发起待办：Q3 竞品分析');
    expect(screen.queryByRole('region', { name: '待办验收' })).not.toBeInTheDocument();
  });

  it('使用紧凑产物与互动层级', async () => {
    render(<TodoDetailPage />);
    await screen.findByText('发起待办：Q3 竞品分析');

    fireEvent.click(screen.getByRole('tab', { name: '产物 4' }));
    const artifactView = screen.getByRole('region', { name: '待办产物' });
    const artifactTitle = within(artifactView).getByText('执行摘要');
    const artifactMeta = within(artifactView).getByText('文本产物');
    const artifactCard = artifactTitle.closest('article');

    expect(artifactTitle).toHaveClass('text-sm', 'font-medium');
    expect(artifactMeta).toHaveClass('text-xs');
    expect(artifactCard).toHaveClass('rounded-xl', 'border', 'border-border', 'bg-card');
    expect(within(artifactView).getByText('已完成三款产品的定价对比。')).toHaveClass('text-xs', 'leading-5');

    fireEvent.click(screen.getByRole('tab', { name: '互动' }));
    const interactionView = screen.getByRole('region', { name: '待办互动' });
    const interactionTitle = within(interactionView).getByRole('heading', { name: '互动' });
    const feedback = within(interactionView).getByText('请补充价格来源');
    const feedbackFloor = feedback.closest('li');
    const marker = within(feedbackFloor as HTMLElement).getByRole('button', { name: '标记为问题点' });
    const sendButton = within(interactionView).getByRole('button', { name: '发送互动' });

    expect(interactionTitle).toHaveClass('text-lg', 'font-semibold');
    expect(feedback).toHaveClass('text-sm', 'leading-6');
    expect(feedbackFloor).toHaveClass('grid', 'grid-cols-[4px_minmax(0,1fr)]', 'gap-4');
    expect(marker).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(marker);
    expect(marker).toHaveAttribute('aria-pressed', 'true');
    expect(sendButton).toHaveClass('h-8', 'rounded-lg', 'px-4', 'text-xs');
  });

  it('使用居中紧凑详情阅读列', async () => {
    render(<TodoDetailPage />);
    const heading = await screen.findByRole('heading', { name: 'Q3 竞品分析' });
    const backButton = screen.getByRole('button', { name: '返回待办列表' });
    const contentColumn = document.querySelector("div[class*='max-w-[820px]']");
    const summary = screen.getByLabelText('待办概要');
    const card = screen.getByRole('group', { name: '待办详情视图' }).closest('.rounded-xl');

    expect(backButton).toHaveClass('size-8', 'rounded-lg');
    expect(backButton.querySelector('svg')).toHaveClass('size-4');
    expect(heading).toHaveClass('text-[18px]', 'font-semibold');
    expect(document.querySelector('header[class*="px-4 pt-6 pb-4"]')).toBeInTheDocument();
    expect(contentColumn).toHaveClass('max-w-[820px]', 'gap-5');

    expect(summary.querySelector('p')).toHaveClass('text-sm', 'leading-[22px]');
    fireEvent.click(screen.getByRole('button', { name: '详情' }));
    expect(screen.getByRole('button', { name: '详情' })).toHaveClass('text-xs', 'font-medium');
    expect(card).toHaveClass('rounded-xl');
  });
});
