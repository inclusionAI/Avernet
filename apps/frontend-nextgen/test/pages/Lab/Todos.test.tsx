/** @jest-environment jsdom */
import type { TodoRecord } from '@/domain/todos/models';
import LabTodosPage from '@/pages/Lab/Todos';
import * as botWorkshop from '@/services/botWorkshop';
import * as todoService from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { history } from '@umijs/max';

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
  loadTodoArtifacts: jest.fn(),
  downloadTodoArtifactContent: jest.fn(),
  startTodoRun: jest.fn(),
}));

jest.mock('@/components/ui/notify', () => ({ notifySuccess: jest.fn() }));
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
  status: 'pending_acceptance',
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
  status: 'pending',
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

beforeEach(() => {
  jest.clearAllMocks();
  resetTodoStore();
  mockedService.loadTodos.mockResolvedValue({
    items: [awaitingAcceptanceTodo, runningTodo],
    total: 2,
    page: 1,
    pageSize: 10,
  });
  mockedService.addTodo.mockResolvedValue(createdTodo);
});

describe('LabTodosPage（待办卡片工作台）', () => {
  it('展示当前空间待办，并用当前空间查询', async () => {
    expect(botWorkshop.botWorkshopService).toBeDefined();
    render(<LabTodosPage />);

    const heading = screen.getByRole('heading', { name: '待办' });
    const description = screen.getByText(/当前空间的 Bot 待办/)
      .closest('p') as HTMLElement;
    expect(heading).toBeInTheDocument();
    expect(heading).toHaveClass('text-[20px]', 'font-semibold', 'leading-[30px]');
    expect(description).toBeInTheDocument();
    expect(description).toHaveClass('text-[13px]', 'font-normal', 'leading-[20px]');
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

  it('展示状态筛选、负责人和搜索，并按负责人查询', async () => {
    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');

    const toolbar = screen.getByRole('region', { name: '待办筛选' });
    const statusGroup = within(toolbar).getByRole('group', { name: '待办状态' });
    const tabs = within(statusGroup).getAllByRole('button');
    expect(tabs.map((tab) => tab.textContent)).toEqual(['全部', '待处理', '进行中', '待验收', '已完成']);

    const owner = within(toolbar).getByRole('combobox', { name: '负责人' });
    const search = within(toolbar).getByRole('textbox', { name: '搜索待办' });
    const createButton = within(toolbar).getByRole('button', { name: '发起待办' });
    expect(statusGroup.compareDocumentPosition(owner) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(owner.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(search.compareDocumentPosition(createButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(owner).toHaveClass('h-8', 'w-40', 'rounded-lg', 'text-xs');
    expect(search).toHaveAttribute('placeholder', '搜索待办标题');
    expect(search).toHaveClass('h-8', 'rounded-lg', 'text-xs');
    expect(createButton).toHaveClass('h-8', 'rounded-lg', 'bg-primary', 'px-4', 'text-xs', 'font-semibold');
    expect(tabs[0]).toHaveClass('h-8', 'bg-primary', 'text-primary-foreground', 'text-xs');

    fireEvent.click(tabs[1]);
    await waitFor(() =>
      expect(mockedService.loadTodos).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'pending' })),
    );

    fireEvent.click(owner);
    fireEvent.click(await screen.findByRole('option', { name: '写作助手' }));
    await waitFor(() =>
      expect(mockedService.loadTodos).toHaveBeenLastCalledWith(expect.objectContaining({ botId: 'bot-owned:u-1' })),
    );
  });

  it('待处理切换回全部时保留条件且不携带状态键', async () => {
    render(<LabTodosPage />);
    const toolbar = await screen.findByRole('region', { name: '待办筛选' });
    const tabs = within(within(toolbar).getByRole('group', { name: '待办状态' })).getAllByRole('button');
    const owner = within(toolbar).getByRole('combobox', { name: '负责人' });
    const search = within(toolbar).getByRole('textbox', { name: '搜索待办' });

    fireEvent.change(search, { target: { value: '编写' } });
    fireEvent.click(owner);
    fireEvent.click(await screen.findByRole('option', { name: '写作助手' }));
    await waitFor(() => {
      const latestQuery = mockedService.loadTodos.mock.calls.at(-1)?.[0];
      expect(latestQuery).toEqual({
        spaceId: 10,
        keyword: '编写',
        botId: 'bot-owned:u-1',
        page: 1,
        pageSize: 10,
      });
      expect(latestQuery).not.toHaveProperty('status');
    });

    fireEvent.click(tabs[1]);
    await waitFor(() =>
      expect(mockedService.loadTodos).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: 'pending', keyword: '编写', botId: 'bot-owned:u-1' }),
      ),
    );

    fireEvent.click(tabs[0]);
    await waitFor(() => {
      const lastQuery = mockedService.loadTodos.mock.calls.at(-1)?.[0];
      expect(lastQuery).toEqual({
        spaceId: 10,
        keyword: '编写',
        botId: 'bot-owned:u-1',
        page: 1,
        pageSize: 10,
      });
      expect(lastQuery).not.toHaveProperty('status');
    });

    expect(await screen.findByText('编写实施方案')).toBeInTheDocument();
  });

  it('创建待办进入待处理，不在前端自动 Run', async () => {
    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');

    fireEvent.click(screen.getByRole('button', { name: '发起待办' }));
    const dialog = await screen.findByRole('dialog');
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
  });

  it('按紧凑卡片和状态分组展示，不显示表格查看操作', async () => {
    render(<LabTodosPage />);

    const acceptanceCard = await screen.findByRole('article', { name: '编写实施方案' });
    expect(within(acceptanceCard).getByText('写作助手')).toBeInTheDocument();
    expect(within(acceptanceCard).getByText('待验收')).toBeInTheDocument();
    const board = screen.getByRole('region', { name: '待办分组' });
    const cardButton = within(acceptanceCard).getByRole('button', { name: /编写实施方案/ });
    const cardGrid = acceptanceCard.parentElement as HTMLElement;
    expect(acceptanceCard).toHaveClass('rounded-xl', 'shadow-sm');
    expect(cardButton).toHaveClass('h-auto', 'rounded-xl', 'p-4');
    expect(cardButton.className).not.toContain('min-h-[218px]');
    expect(cardGrid).toHaveClass(
      'gap-[10px]',
      '[grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr))]',
    );
    expect(within(acceptanceCard).getByText('编写实施方案', { selector: 'span' })).toHaveClass('truncate', 'text-sm', 'font-medium');
    expect(within(acceptanceCard).getByText('整理需求并输出实施方案')).toHaveClass('line-clamp-2', 'text-xs', 'leading-[18px]');
    expect(within(board).getByRole('button', { name: /^待处理 \(1\)$/ })).toHaveTextContent('1');
    expect(within(board).getByRole('button', { name: /^进行中 \(1\)$/ })).toHaveTextContent('1');
    expect(screen.queryByRole('columnheader')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看' })).not.toBeInTheDocument();
  });

  it('点击卡片进入独立待办详情页', async () => {
    render(<LabTodosPage />);

    const card = await screen.findByRole('article', { name: '编写实施方案' });
    fireEvent.click(within(card).getByRole('button', { name: /编写实施方案/ }));

    expect(mockedHistory.push).toHaveBeenCalledWith('/lab/todos/todo-1');
  });
});
