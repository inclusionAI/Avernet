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

jest.mock('@/services/backendApi/collaboration/collaborationBotController', () => ({
  queryCollaborationBots: jest.fn(async () => ({
    code: 200000,
    message: 'success',
    data: {
      total: 1,
      items: [
        {
          kind: 'bot',
          bot_id: 'bot-owned',
          created_by: 'u-1',
          visibility: 'public',
        },
      ],
    },
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
  loadTodoSpaces: jest.fn(),
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
  createTodoAutoImportRule: jest.fn(),
  loadTodoAutoImportRules: jest.fn(),
  updateTodoAutoImportRule: jest.fn(),
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

const pendingTodo: TodoRecord = {
  ...awaitingAcceptanceTodo,
  todoId: 'todo-pending',
  title: '准备发布说明',
  status: 'pending',
  collabContextId: undefined,
};

const completedTodo: TodoRecord = {
  ...awaitingAcceptanceTodo,
  todoId: 'todo-completed',
  title: '复盘上线结果',
  status: 'completed',
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
  localStorage.clear();
  resetTodoStore();
  mockedService.loadTodoSpaces.mockResolvedValue([mockPersonalSpace]);
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
});

describe('LabTodosPage（待办卡片工作台）', () => {
  it('Engine 空间加载失败时展示错误，重试成功后再请求待办', async () => {
    mockedService.loadTodoSpaces.mockRejectedValueOnce(new Error('加载空间列表失败'));

    render(<LabTodosPage />);

    expect(await screen.findByText('加载空间列表失败')).toBeInTheDocument();
    expect(screen.queryByText('加载待办中…')).not.toBeInTheDocument();
    expect(mockedService.loadTodos).not.toHaveBeenCalled();
    expect(mockedService.loadTodoSpaces).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    await waitFor(() => expect(mockedService.loadTodoSpaces).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockedService.loadTodos).toHaveBeenCalledWith(expect.objectContaining({ spaceId: 10 })));
  });

  it('展示当前空间待办，并用当前空间查询', async () => {
    expect(botWorkshop.botWorkshopService).toBeDefined();
    render(<LabTodosPage />);

    const heading = screen.getByRole('heading', { name: '待办' });
    const description = screen.getByText(/当前空间的 Bot 待办/).closest('p') as HTMLElement;
    expect(heading).toBeInTheDocument();
    expect(heading).toHaveClass('text-[20px]', 'font-semibold', 'leading-[30px]');
    expect(description).toBeInTheDocument();
    expect(description).toHaveClass('text-[13px]', 'font-normal', 'leading-[20px]');
    expect(await screen.findByText('测试用户的个人空间')).toBeInTheDocument();
    expect(screen.queryByText('个人空间')).not.toBeInTheDocument();

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
    const importButton = within(toolbar).getByRole('button', { name: '导入 Dima' });
    const createButton = within(toolbar).getByRole('button', { name: '发起待办' });
    expect(statusGroup.compareDocumentPosition(owner) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(owner.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(search.compareDocumentPosition(importButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(importButton.compareDocumentPosition(createButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(owner).toHaveClass('h-8', 'w-40', 'rounded-lg', 'text-xs');
    expect(search).toHaveAttribute('placeholder', '搜索待办标题');
    expect(search).toHaveClass('h-8', 'rounded-lg', 'text-xs');
    expect(importButton).toHaveClass('h-8', 'rounded-lg', 'px-4', 'text-xs', 'font-semibold');
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

  it('状态筛选只展示对应状态的待办和分组', async () => {
    mockedService.loadTodos.mockResolvedValue({
      items: [pendingTodo, runningTodo, awaitingAcceptanceTodo, completedTodo],
      total: 4,
      page: 1,
      pageSize: 10,
    });
    render(<LabTodosPage />);
    const toolbar = await screen.findByRole('region', { name: '待办筛选' });
    const statusGroup = within(toolbar).getByRole('group', { name: '待办状态' });

    fireEvent.click(within(statusGroup).getByRole('button', { name: '进行中' }));
    expect(await screen.findByRole('article', { name: '整理会议纪要' })).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: '准备发布说明' })).not.toBeInTheDocument();
    expect(screen.queryByRole('article', { name: '编写实施方案' })).not.toBeInTheDocument();
    expect(screen.queryByRole('article', { name: '复盘上线结果' })).not.toBeInTheDocument();
    expect(document.querySelector('#todo-group-in_progress')).toHaveTextContent('进行中');
    expect(within(screen.getByRole('region', { name: '待办分组' })).queryByText('待处理')).not.toBeInTheDocument();

    fireEvent.click(within(statusGroup).getByRole('button', { name: '待验收' }));
    expect(await screen.findByRole('article', { name: '编写实施方案' })).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: '整理会议纪要' })).not.toBeInTheDocument();
    expect(screen.queryByRole('article', { name: '复盘上线结果' })).not.toBeInTheDocument();
    expect(document.querySelector('#todo-group-pending_acceptance')).toHaveTextContent('待验收');

    fireEvent.click(within(statusGroup).getByRole('button', { name: '已完成' }));
    expect(await screen.findByRole('article', { name: '复盘上线结果' })).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: '编写实施方案' })).not.toBeInTheDocument();
    expect(screen.queryByRole('article', { name: '整理会议纪要' })).not.toBeInTheDocument();
    expect(document.querySelector('#todo-group-completed')).toHaveTextContent('已完成');
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
    expect(within(dialog).getByRole('heading', { name: '创建待办' })).toBeInTheDocument();
    expect(within(dialog).queryByText('选择 Dima 来源')).not.toBeInTheDocument();
    expect(mockedService.loadTodoSourceProjects).not.toHaveBeenCalled();
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

  it('从 Dima 工作项导入可编辑内容并携带稳定来源身份', async () => {
    mockedService.loadTodoSourceProjects.mockResolvedValue([
      { id: 'W1', name: '支付空间', kind: 'workspace' },
      { id: 'P1', name: '支付项目', kind: 'project' },
    ]);
    mockedService.loadTodoSourceWorkItems.mockResolvedValue([
      {
        id: '2026112000000000001',
        projectId: 'W1',
        category: 'req',
        title: '优化支付体验',
        status: '处理中',
      },
    ]);
    mockedService.loadTodoSourceDraft.mockResolvedValue({
      sourceType: 'dima',
      projectId: 'W1',
      workItemId: '2026112000000000001',
      category: 'req',
      title: '优化支付体验',
      description: '来源：Dima REQ 2026112000000000001\n\n原始需求描述',
      sourceUrl: 'https://dima.example/items/2026112000000000001',
      sourceUpdatedAt: '2026-11-20T12:00:00Z',
      metadata: {
        processor_id: '149844',
        processor_name: '张三',
      },
    });

    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');
    fireEvent.click(screen.getByRole('button', { name: '导入 Dima' }));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByRole('heading', { name: '导入 Dima 待办' })).toBeInTheDocument();
    const importMode = within(dialog).getByRole('group', { name: 'Dima 导入方式' });
    expect(within(importMode).getByRole('button', { name: '手动导入' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(importMode).getByRole('button', { name: '自动导入' })).toHaveAttribute('aria-pressed', 'false');
    await waitFor(() => expect(mockedService.loadTodoSourceProjects).toHaveBeenCalledTimes(1));
    expect(within(dialog).getByText('当前账号可访问 1 个空间、1 个项目')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: '选择 Dima 空间 支付空间' }));
    await waitFor(() => expect(mockedService.loadTodoSourceWorkItems).toHaveBeenCalledWith('W1'));

    fireEvent.click(within(dialog).getByRole('button', { name: '导入 Dima 工作项 优化支付体验' }));
    await waitFor(() => expect(mockedService.loadTodoSourceDraft).toHaveBeenCalledWith('W1', '2026112000000000001'));
    expect(within(dialog).getByRole('textbox', { name: '标题' })).toHaveValue('优化支付体验');

    fireEvent.change(within(dialog).getByRole('textbox', { name: '标题' }), {
      target: { value: '优化支付体验（待办）' },
    });
    fireEvent.change(within(dialog).getByRole('textbox', { name: '描述' }), {
      target: { value: '整理后的执行要求' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '导入待办' }));

    await waitFor(() =>
      expect(mockedService.addTodo).toHaveBeenCalledWith({
        spaceId: 10,
        title: '优化支付体验（待办）',
        description: '整理后的执行要求',
        botId: 'bot-owned:u-1',
        source: {
          type: 'dima',
          containerId: 'W1',
          itemId: '2026112000000000001',
          url: 'https://dima.example/items/2026112000000000001',
          updatedAt: '2026-11-20T12:00:00Z',
          metadata: {
            processor_id: '149844',
            processor_name: '张三',
          },
        },
      }),
    );
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();
  });

  it('配置多空间 Dima 自动导入规则并可停用', async () => {
    mockedService.loadTodoSourceProjects.mockResolvedValue([
      { id: 'W1', name: '支付空间', kind: 'workspace' },
      { id: 'P1', name: '支付项目', kind: 'project' },
    ]);
    const enabledRule = {
      ruleId: 'rule-1',
      spaceId: 10,
      sourceContainerIds: ['W1', 'P1'],
      filterText: '只导入需要研发处理且尚未完成的事项',
      botId: 'bot-owned:u-1',
      status: 'enabled' as const,
      createdAt: '2026-11-25T07:00:00+00:00',
      updatedAt: '2026-11-25T07:00:00+00:00',
    };
    mockedService.createTodoAutoImportRule.mockResolvedValue(enabledRule);
    mockedService.updateTodoAutoImportRule.mockResolvedValue({ ...enabledRule, status: 'disabled' });

    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');
    fireEvent.click(screen.getByRole('button', { name: '导入 Dima' }));
    const dialog = await screen.findByRole('dialog');
    const importMode = within(dialog).getByRole('group', { name: 'Dima 导入方式' });
    fireEvent.click(within(importMode).getByRole('button', { name: '自动导入' }));

    await waitFor(() => expect(mockedService.loadTodoAutoImportRules).toHaveBeenCalledWith(10));
    fireEvent.click(await within(dialog).findByRole('checkbox', { name: '选择自动导入 Dima 空间 支付空间' }));
    fireEvent.click(within(dialog).getByRole('checkbox', { name: '选择自动导入 Dima 项目 支付项目' }));
    fireEvent.change(within(dialog).getByRole('textbox', { name: '自动导入过滤条件' }), {
      target: { value: '只导入需要研发处理且尚未完成的事项' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '保存自动导入规则' }));

    await waitFor(() =>
      expect(mockedService.createTodoAutoImportRule).toHaveBeenCalledWith({
        spaceId: 10,
        sourceContainerIds: ['W1', 'P1'],
        filterText: '只导入需要研发处理且尚未完成的事项',
        botId: 'bot-owned:u-1',
      }),
    );
    expect(await within(dialog).findByText('只导入需要研发处理且尚未完成的事项')).toBeInTheDocument();

    fireEvent.click(
      within(dialog).getByRole('switch', {
        name: '停用自动导入规则 只导入需要研发处理且尚未完成的事项',
      }),
    );
    await waitFor(() =>
      expect(mockedService.updateTodoAutoImportRule).toHaveBeenCalledWith('rule-1', { status: 'disabled' }),
    );
  });

  it('Dima 来源失败时展示可恢复错误且不创建待办', async () => {
    mockedService.loadTodoSourceProjects.mockRejectedValue(new Error('provider detail'));

    render(<LabTodosPage />);
    await screen.findByText('编写实施方案');
    fireEvent.click(screen.getByRole('button', { name: '导入 Dima' }));
    const dialog = await screen.findByRole('dialog');

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Dima 空间与项目加载失败，请重试。');
    expect(within(dialog).getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(mockedService.addTodo).not.toHaveBeenCalled();
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
    expect(cardGrid).toHaveClass('gap-[10px]', '[grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr))]');
    expect(within(acceptanceCard).getByText('编写实施方案', { selector: 'span' })).toHaveClass(
      'truncate',
      'text-sm',
      'font-medium',
    );
    expect(within(acceptanceCard).getByText('整理需求并输出实施方案')).toHaveClass(
      'line-clamp-2',
      'text-xs',
      'leading-[18px]',
    );
    expect(within(board).getByRole('button', { name: /^待处理 \(0\)$/ })).toHaveTextContent('0');
    expect(within(board).getByRole('button', { name: /^进行中 \(1\)$/ })).toHaveTextContent('1');
    expect(within(board).getByRole('button', { name: /^待验收 \(1\)$/ })).toHaveTextContent('1');
    expect(
      within(
        within(board)
          .getByRole('button', { name: /^待处理 \(0\)$/ })
          .closest('section') as HTMLElement,
      ).queryByRole('article', { name: '编写实施方案' }),
    ).not.toBeInTheDocument();
    expect(
      within(
        within(board)
          .getByRole('button', { name: /^待验收 \(1\)$/ })
          .closest('section') as HTMLElement,
      ).getByRole('article', { name: '编写实施方案' }),
    ).toBeInTheDocument();
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
