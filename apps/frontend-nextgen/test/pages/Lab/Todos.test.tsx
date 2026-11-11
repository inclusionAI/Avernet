/** @jest-environment jsdom */
import type { TodoEvent, TodoRecord } from '@/domain/todos/models';
import LabTodosPage from '@/pages/Lab/Todos';
import * as botWorkshop from '@/services/botWorkshop';
import * as todoService from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn(), replace: jest.fn() },
  useLocation: () => ({ pathname: '/lab/todos' }),
}));

jest.mock('@/hooks/useSpaceContext', () => {
  const currentSpace = {
    spaceId: 10,
    spaceCode: 'personal-u-1',
    spaceName: '测试用户的个人空间',
    spaceType: 'PERSONAL',
    currentUserRole: 'ADMIN',
    memberCount: 1,
    ownerCount: 1,
    botCount: 1,
    gmtModified: '2026-11-10T01:00:00Z',
  };
  const state = { currentSpace, currentSpaceId: 10, initialized: true };
  const init = jest.fn(async () => undefined);
  return {
    useSpaceContext: (selector: (value: typeof state) => unknown) => selector(state),
    useSpaceContextActions: () => ({ init, refresh: jest.fn(), switchSpace: jest.fn() }),
  };
});

jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: jest.fn(() => ({
    identity: { userId: 'u-1', displayName: '测试用户' },
    status: 'ready',
  })),
}));

jest.mock('@/services/botWorkshop', () => ({
  botWorkshopService: {
    list: jest.fn(async () => ({
      items: [
        { id: 'bot-foreign:u-2', name: '他人助手', ownerId: 'u-2' },
        { id: 'bot-owned:u-1', name: '写作助手', ownerId: 'u-1' },
      ],
      total: 2,
      page: 1,
      pageSize: 100,
      hasMore: false,
    })),
  },
}));

jest.mock('@/services/todos/todoService', () => ({
  loadTodos: jest.fn(),
  loadTodo: jest.fn(),
  addTodo: jest.fn(),
  acceptTodo: jest.fn(),
  loadTodoInteractions: jest.fn(),
  addTodoInteraction: jest.fn(),
  loadTodoEvents: jest.fn(),
  startTodoRun: jest.fn(),
}));

jest.mock('@/components/ui/notify', () => ({ notifySuccess: jest.fn() }));
jest.mock('@/utils/errorToast', () => ({ safeReportError: jest.fn() }));

Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= () => undefined;
Element.prototype.releasePointerCapture ??= () => undefined;
Element.prototype.scrollIntoView ??= () => undefined;

const mockedService = jest.mocked(todoService);

const pendingTodo: TodoRecord = {
  todoId: 'todo-1',
  spaceId: 10,
  title: '编写实施方案',
  description: '整理需求并输出实施方案',
  botId: 'bot-owned:u-1',
  botName: '写作助手',
  status: 'pending_acceptance',
  collabContextId: 'ctx-1',
  createdAt: '2026-11-10T01:00:00Z',
  updatedAt: '2026-11-10T02:00:00Z',
};

const createdTodo: TodoRecord = {
  ...pendingTodo,
  todoId: 'todo-new',
  title: '分析本周用户反馈',
  description: '输出问题分类和改进建议',
  status: 'draft',
};

const draftTodo: TodoRecord = { ...pendingTodo, status: 'draft' };

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
    createdAt: `2026-11-10T02:0${eventSeq}:00Z`,
    artifacts: [],
    ...overrides,
  };
}

function resetTodoStore() {
  useTodoStore.setState({
    todos: [],
    total: 0,
    selectedTodo: null,
    interactions: [],
    events: [],
    listLoading: false,
    detailLoading: false,
    eventsLoading: false,
    mutationLoading: false,
    listError: null,
    detailError: null,
    eventsError: null,
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  resetTodoStore();
  mockedService.loadTodos.mockResolvedValue({ items: [pendingTodo], total: 1, page: 1, pageSize: 10 });
  mockedService.loadTodo.mockResolvedValue(pendingTodo);
  mockedService.loadTodoInteractions.mockResolvedValue([
    event(3, {
      eventType: 'user_interaction.posted',
      actorId: 'u-1',
      actorType: 'human',
      content: '请补充风险说明',
    }),
  ]);
  mockedService.loadTodoEvents.mockResolvedValue([
    event(1, { content: '开始整理方案', status: 'in_progress' }),
    event(2, {
      content: '已完成方案',
      status: 'pending_acceptance',
      artifacts: [{ artifactId: 'artifact-1', name: '实施方案.md', url: 'https://example.test/plan' }],
    }),
  ]);
  mockedService.addTodo.mockResolvedValue(createdTodo);
  mockedService.addTodoInteraction.mockResolvedValue(
    event(4, { eventType: 'user_interaction.posted', actorId: 'u-1', actorType: 'human', content: '补充回滚方案' }),
  );
  mockedService.acceptTodo.mockResolvedValue({ ...pendingTodo, status: 'completed' });
  mockedService.startTodoRun.mockResolvedValue({ ...pendingTodo, status: 'in_progress' });
});

describe('LabTodosPage（待办工作台）', () => {
  it('展示当前空间待办，并用当前空间查询', async () => {
    expect(botWorkshop.botWorkshopService).toBeDefined();
    render(<LabTodosPage />);

    expect(screen.getByRole('heading', { name: '待办' })).toBeInTheDocument();
    expect(screen.getByText(/当前空间的 Bot 待办/)).toBeInTheDocument();
    expect(screen.getByText('测试用户的个人空间')).toBeInTheDocument();

    await waitFor(() =>
      expect(mockedService.loadTodos).toHaveBeenCalledWith({
        spaceId: 10,
        keyword: undefined,
        status: undefined,
        botId: undefined,
        page: 1,
        pageSize: 10,
      }),
    );
  });

  it('按原型顺序展示状态 Tab、负责人和搜索，并按负责人查询', async () => {
    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');

    const toolbar = screen.getByRole('region', { name: '待办筛选' });
    const tabs = within(toolbar).getAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual(['全部', '待处理', '进行中', '已完成', '草稿']);

    const owner = within(toolbar).getByRole('combobox', { name: '负责人' });
    const search = within(toolbar).getByRole('textbox', { name: '搜索待办' });
    expect(owner.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(search).toHaveAttribute('placeholder', '搜索待办标题');
    expect(within(toolbar).queryByText('团队知识库')).not.toBeInTheDocument();

    fireEvent.click(tabs[1]);
    await waitFor(() =>
      expect(mockedService.loadTodos).toHaveBeenLastCalledWith({
        spaceId: 10,
        keyword: undefined,
        status: 'pending_acceptance',
        botId: undefined,
        page: 1,
        pageSize: 10,
      }),
    );

    fireEvent.click(owner);
    fireEvent.click(await screen.findByRole('option', { name: '写作助手' }));
    await waitFor(() =>
      expect(mockedService.loadTodos).toHaveBeenLastCalledWith({
        spaceId: 10,
        keyword: undefined,
        status: 'pending_acceptance',
        botId: 'bot-owned:u-1',
        page: 1,
        pageSize: 10,
      }),
    );
  });

  it('创建待办创建为草稿，不在前端自动 Run', async () => {
    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');

    fireEvent.click(screen.getByRole('button', { name: '发起待办' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('combobox', { name: '选择 Bot' })).toHaveTextContent('写作助手');

    fireEvent.change(within(dialog).getByRole('textbox', { name: '标题' }), {
      target: { value: ' 分析本周用户反馈 ' },
    });
    fireEvent.change(within(dialog).getByRole('textbox', { name: '描述' }), {
      target: { value: '输出问题分类和改进建议' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '创建待办' }));

    await waitFor(() =>
      expect(mockedService.addTodo).toHaveBeenCalledWith({
        spaceId: 10,
        title: '分析本周用户反馈',
        description: '输出问题分类和改进建议',
        botId: 'bot-owned:u-1',
      }),
    );
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('展示待办列表、Bot 名称和状态', async () => {
    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');

    const row = screen.getByText('编写实施方案').closest('tr');
    expect(within(row!).getByText('写作助手')).toBeInTheDocument();
    expect(within(row!).getByText('待处理')).toBeInTheDocument();
    expect(within(row!).getByRole('button', { name: '查看' })).toBeInTheDocument();
  });

  it('详情展示 B 事件时序、互动和产物名称地址', async () => {
    render(<LabTodosPage />);
    const row = (await screen.findByText('编写实施方案')).closest('tr');
    fireEvent.click(within(row!).getByRole('button', { name: '查看' }));

    expect(await screen.findAllByRole('heading', { name: '编写实施方案' })).toHaveLength(2);
    expect(await screen.findByText('已完成方案')).toBeInTheDocument();
    expect(screen.getByText('请补充风险说明')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /实施方案\.md/ })).toHaveAttribute('href', 'https://example.test/plan');
    expect(mockedService.loadTodoEvents).toHaveBeenCalledWith('todo-1');
  });

  it('待验收互动仅提交内容，B 负责推进后续执行', async () => {
    render(<LabTodosPage />);
    const row = (await screen.findByText('编写实施方案')).closest('tr');
    fireEvent.click(within(row!).getByRole('button', { name: '查看' }));
    await screen.findByText('已完成方案');

    fireEvent.change(screen.getByPlaceholderText('告诉 Bot 需要补充或修改的内容'), {
      target: { value: ' 补充回滚方案 ' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送互动' }));

    await waitFor(() =>
      expect(mockedService.addTodoInteraction).toHaveBeenCalledWith('todo-1', { message: '补充回滚方案' }),
    );
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();
  });

  it('拒绝验收只提交验收决策，不在前端调用 Run', async () => {
    mockedService.acceptTodo.mockResolvedValue(pendingTodo);

    render(<LabTodosPage />);
    const row = (await screen.findByText('编写实施方案')).closest('tr');
    fireEvent.click(within(row!).getByRole('button', { name: '查看' }));
    await screen.findByText('已完成方案');

    fireEvent.change(screen.getByPlaceholderText('可选：填写验收意见'), { target: { value: ' 请修改 ' } });
    fireEvent.click(screen.getByRole('button', { name: '验收不通过' }));

    await waitFor(() =>
      expect(mockedService.acceptTodo).toHaveBeenCalledWith('todo-1', {
        verdict: 'rejected',
        message: '请修改',
      }),
    );
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();
  });

  it('草稿支持显式启动执行', async () => {
    mockedService.loadTodo.mockResolvedValueOnce(draftTodo);

    render(<LabTodosPage />);
    const row = (await screen.findByText('编写实施方案')).closest('tr');
    fireEvent.click(within(row!).getByRole('button', { name: '查看' }));

    fireEvent.click(await screen.findByRole('button', { name: '启动执行' }));

    await waitFor(() => expect(mockedService.startTodoRun).toHaveBeenCalledWith('todo-1'));
    expect(screen.queryByRole('button', { name: '编辑待办' })).not.toBeInTheDocument();
  });

  it('非草稿不展示编辑和启动入口', async () => {
    render(<LabTodosPage />);
    const row = (await screen.findByText('编写实施方案')).closest('tr');
    fireEvent.click(within(row!).getByRole('button', { name: '查看' }));
    await screen.findByText('已完成方案');

    expect(screen.queryByRole('button', { name: '编辑待办' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启动执行' })).not.toBeInTheDocument();
  });

});
