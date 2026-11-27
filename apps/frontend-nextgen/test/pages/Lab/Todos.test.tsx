/** @jest-environment jsdom */
import type { TodoRecord } from '@/domain/todos/models';
import LabTodosPage from '@/pages/Lab/Todos';
import * as todoService from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { history } from '@umijs/max';

const mockPersonalSpace = {
  spaceId: 10,
  spaceName: '测试用户的个人空间',
  spaceType: 'PERSONAL' as const,
};

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn(), replace: jest.fn() },
  useLocation: () => ({ pathname: '/lab/todos' }),
}));

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: jest.fn(() => ({
    identity: { userId: 'u-1', displayName: '测试用户' },
    status: 'ready',
  })),
}));

jest.mock('@/services/todos/todoService', () => ({
  loadTodoSpaces: jest.fn(),
  loadTodoBots: jest.fn(),
  loadTodoOwners: jest.fn(),
  loadTodos: jest.fn(),
  loadTodo: jest.fn(),
  addTodo: jest.fn(),
  acceptTodo: jest.fn(),
  loadTodoInteractions: jest.fn(),
  addTodoInteraction: jest.fn(),
  loadTodoEvents: jest.fn(),
  loadTodoArtifacts: jest.fn(),
  loadTodoMentionBots: jest.fn(),
  downloadTodoArtifactContent: jest.fn(),
  startTodoRun: jest.fn(),
  loadTodoSourceProjects: jest.fn(),
  loadTodoSourceWorkItems: jest.fn(),
  loadTodoSourceDraft: jest.fn(),
  importTodoDimaItems: jest.fn(),
  createTodoAutoImportRule: jest.fn(),
  loadTodoAutoImportRules: jest.fn(),
  updateTodoAutoImportRule: jest.fn(),
  loadBotTeams: jest.fn(),
  loadBotTeam: jest.fn(),
  addBotTeam: jest.fn(),
  saveBotTeam: jest.fn(),
  archiveTodoBotTeam: jest.fn(),
}));

jest.mock('@/components/ui/notify', () => ({
  notifySuccess: jest.fn(),
  notifyWarning: jest.fn(),
}));
jest.mock('@/utils/errorToast', () => ({ safeReportError: jest.fn() }));

Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= () => undefined;
Element.prototype.releasePointerCapture ??= () => undefined;
Element.prototype.scrollIntoView ??= () => undefined;

const mockedService = jest.mocked(todoService);
const mockedHistory = jest.mocked(history);

const awaitingAcceptanceTodo: TodoRecord = {
  todoId: 'todo-1',
  spaceId: 10,
  title: '编写实施方案',
  description: '整理需求并输出实施方案',
  botId: 'bot-owned:u-1',
  botName: '写作助手',
  ownerId: 'u-1',
  ownerName: '测试用户',
  status: 'pending_action',
  collabContextId: 'ctx-1',
  createdAt: '2026-11-10T01:00:00Z',
  updatedAt: '2026-11-10T02:00:00Z',
};

const runningTodo: TodoRecord = {
  ...awaitingAcceptanceTodo,
  todoId: 'todo-2',
  title: '整理会议纪要',
  status: 'in_progress',
};

const createdTodo: TodoRecord = {
  ...awaitingAcceptanceTodo,
  todoId: 'todo-new',
  title: '分析本周用户反馈',
  description: '输出问题分类和改进建议',
  status: 'not_started',
  collabContextId: undefined,
};

function resetTodoStore() {
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

async function chooseSelect(triggerName: string, optionName: string) {
  fireEvent.click(screen.getByRole('combobox', { name: triggerName }));
  fireEvent.click(await screen.findByRole('option', { name: optionName }));
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  resetTodoStore();
  mockedService.loadTodoSpaces.mockResolvedValue([mockPersonalSpace]);
  mockedService.loadTodoBots.mockResolvedValue([
    { id: 'bot-foreign:u-2', name: '他人助手', ownerId: 'u-2' },
    { id: 'bot-owned:u-1', name: '写作助手', ownerId: 'u-1' },
  ] as never);
  mockedService.loadTodoOwners.mockResolvedValue([
    { userId: 'u-1', userName: '测试用户' },
  ]);
  mockedService.loadTodos.mockResolvedValue({
    items: [awaitingAcceptanceTodo, runningTodo],
    total: 2,
    page: 1,
    pageSize: 10,
  });
  mockedService.addTodo.mockResolvedValue(createdTodo);
  mockedService.loadTodoSourceProjects.mockResolvedValue([]);
  mockedService.loadTodoSourceWorkItems.mockResolvedValue([]);
  mockedService.loadTodoAutoImportRules.mockResolvedValue([]);
  mockedService.loadBotTeams.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 100 });
  mockedService.importTodoDimaItems.mockResolvedValue([]);
});

describe('LabTodosPage（产品待办工作台）', () => {
  it('空间加载失败时可重试，空间就绪后再请求待办', async () => {
    mockedService.loadTodoSpaces.mockRejectedValueOnce(new Error('加载空间列表失败'));
    render(<LabTodosPage />);

    expect(await screen.findByText('加载空间列表失败')).toBeInTheDocument();
    expect(mockedService.loadTodos).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(mockedService.loadTodoSpaces).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockedService.loadTodos).toHaveBeenCalledWith({
      spaceId: 10,
      page: 1,
      pageSize: 10,
    }));
  });

  it('按原型展示待办列表、筛选和产品入口', async () => {
    render(<LabTodosPage />);

    expect(screen.getByRole('heading', { name: '待办' })).toBeInTheDocument();
    expect(screen.getByText('在同一空间内，让 Bot 与团队协作，与团队一起完成工作')).toBeInTheDocument();
    const table = await screen.findByRole('table', { name: '待办列表' });
    expect(within(table).getByText('编写实施方案')).toBeInTheDocument();
    expect(within(table).getByText('整理会议纪要')).toBeInTheDocument();
    expect(within(table).getByRole('columnheader', { name: '执行方' })).toBeInTheDocument();
    expect(within(table).getByRole('columnheader', { name: '负责人' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '待办状态' })).toHaveTextContent('全部状态 (2)');
    expect(screen.getByRole('combobox', { name: '负责人' })).toHaveTextContent('全部负责人');
    expect(screen.getByRole('button', { name: '自动化' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Bot 小队' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '从 Dima 导入' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发起待办' })).toBeInTheDocument();
  });

  it('状态和搜索条件进入后端查询，执行方在当前页过滤', async () => {
    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');

    await chooseSelect('待办状态', '进行中');
    await waitFor(() => expect(mockedService.loadTodos).toHaveBeenLastCalledWith({
      spaceId: 10,
      status: 'in_progress',
      page: 1,
      pageSize: 10,
    }));

    fireEvent.change(screen.getByRole('textbox', { name: '搜索待办' }), { target: { value: '会议' } });
    await waitFor(() => expect(mockedService.loadTodos).toHaveBeenLastCalledWith({
      spaceId: 10,
      keyword: '会议',
      status: 'in_progress',
      page: 1,
      pageSize: 10,
    }));

    await chooseSelect('负责人', '写作助手');
    expect(screen.getByRole('combobox', { name: '负责人' })).toHaveTextContent('写作助手');
    expect(mockedService.loadTodos.mock.calls.at(-1)?.[0]).not.toHaveProperty('botId');
  });

  it('创建待办提交执行方和人工负责人，不在前端自动 Run', async () => {
    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');

    fireEvent.click(screen.getByRole('button', { name: '发起待办' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: '发起待办' })).toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole('textbox', { name: '标题' }), {
      target: { value: ' 分析本周用户反馈 ' },
    });
    fireEvent.change(within(dialog).getByRole('textbox', { name: '正文' }), {
      target: { value: '输出问题分类和改进建议' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '创建待办' }));

    await waitFor(() => expect(mockedService.addTodo).toHaveBeenCalledWith({
      spaceId: 10,
      title: '分析本周用户反馈',
      description: '输出问题分类和改进建议',
      executor: { type: 'bot', id: 'bot-owned:u-1' },
      owner: { userId: 'u-1', userName: '测试用户' },
    }));
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();
  });

  it('从 Dima 多选导入并携带稳定来源身份', async () => {
    mockedService.loadTodoSourceProjects.mockResolvedValue([{ id: 'P1', name: '支付项目', kind: 'project' }]);
    mockedService.loadTodoSourceWorkItems.mockResolvedValue([{
      id: '2026112000000000001',
      projectId: 'P1',
      category: 'req',
      title: '优化支付体验',
      status: '处理中',
    }]);
    mockedService.loadTodoSourceDraft.mockResolvedValue({
      sourceType: 'dima',
      projectId: 'P1',
      workItemId: '2026112000000000001',
      category: 'req',
      title: '优化支付体验',
      description: '原始需求描述',
      sourceUrl: 'https://dima.example/items/2026112000000000001',
      sourceUpdatedAt: '2026-11-20T12:00:00Z',
      metadata: { processor_name: '张三', priority: '高' },
    });
    mockedService.importTodoDimaItems.mockResolvedValue([{
      sourceWorkItemId: '2026112000000000001',
      status: 'imported',
      todoId: 'todo-dima-1',
      productRuntimeStatus: 'ready',
      retryable: false,
    }]);

    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');
    fireEvent.click(screen.getByRole('button', { name: '从 Dima 导入' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: '从 Dima 导入' })).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('combobox', { name: '选择 Dima 空间或项目' }));
    fireEvent.click(await screen.findByRole('option', { name: '支付项目' }));
    await waitFor(() => expect(mockedService.loadTodoSourceWorkItems).toHaveBeenCalledWith('P1'));
    fireEvent.click(await within(dialog).findByRole('checkbox', { name: '选择 优化支付体验' }));
    await waitFor(() => expect(mockedService.loadTodoSourceDraft).toHaveBeenCalledWith('P1', '2026112000000000001'));
    fireEvent.click(within(dialog).getByRole('button', { name: '导入 1 个待办' }));

    await waitFor(() => expect(mockedService.importTodoDimaItems).toHaveBeenCalledWith({
      spaceId: 10,
      executor: { type: 'bot', id: 'bot-owned:u-1' },
      owner: { userId: 'u-1', userName: '测试用户' },
      items: [{
        title: '优化支付体验',
        description: '原始需求描述',
        source: {
          type: 'dima',
          containerId: 'P1',
          itemId: '2026112000000000001',
          url: 'https://dima.example/items/2026112000000000001',
          updatedAt: '2026-11-20T12:00:00Z',
          metadata: { processor_name: '张三', priority: '高' },
        },
      }],
    }));
    expect(await within(dialog).findByText('成功 1')).toBeInTheDocument();
  });

  it('Dima 来源失败时展示可恢复错误且不执行导入', async () => {
    mockedService.loadTodoSourceProjects.mockRejectedValue(new Error('provider detail'));
    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');
    fireEvent.click(screen.getByRole('button', { name: '从 Dima 导入' }));
    const dialog = await screen.findByRole('dialog');

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Dima 空间与项目加载失败，请重试。');
    expect(within(dialog).getByRole('button', { name: '重新加载项目' })).toBeInTheDocument();
    expect(mockedService.importTodoDimaItems).not.toHaveBeenCalled();
  });

  it('可进入 Bot 小队和自动化独立工作区', async () => {
    const { rerender } = render(<LabTodosPage />);
    await screen.findByText('编写实施方案');
    fireEvent.click(screen.getByRole('button', { name: 'Bot 小队' }));
    expect(await screen.findByRole('heading', { name: 'Bot 小队' })).toBeInTheDocument();
    expect(mockedService.loadBotTeams).toHaveBeenCalledWith(10, 'active');

    fireEvent.click(screen.getByRole('button', { name: '返回待办' }));
    rerender(<LabTodosPage />);
    fireEvent.click(await screen.findByRole('button', { name: '自动化' }));
    expect(await screen.findByRole('heading', { name: '自动化' })).toBeInTheDocument();
    expect(mockedService.loadTodoAutoImportRules).toHaveBeenCalledWith(10);
  });

  it('点击表格行进入独立待办详情页', async () => {
    render(<LabTodosPage />);
    const row = await screen.findByRole('row', { name: /编写实施方案/ });
    fireEvent.click(row);
    expect(mockedHistory.push).toHaveBeenCalledWith('/lab/todos/todo-1');
  });
});
