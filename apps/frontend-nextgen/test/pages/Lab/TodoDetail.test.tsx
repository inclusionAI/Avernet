/** @jest-environment jsdom */
import type { TodoEvent, TodoRecord } from '@/domain/todos/models';
import TodoDetailPage from '@/pages/Lab/Todos/TodoDetailPage';
import * as todoService from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import { history } from '@umijs/max';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

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

jest.mock('@/services/todos/todoService', () => ({
  loadTodo: jest.fn(),
  acceptTodo: jest.fn(),
  loadTodoInteractions: jest.fn(),
  addTodoInteraction: jest.fn(),
  loadTodoEvents: jest.fn(),
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
  resetStore();
  mockedService.loadTodo.mockResolvedValue(todo);
  mockedService.loadTodoInteractions.mockResolvedValue([
    event(4, { eventType: 'user_interaction.posted', actorType: 'human', content: '请补充价格来源' }),
  ]);
  mockedService.loadTodoEvents.mockResolvedValue([
    event(1, { content: '开始采集竞品资料' }),
    event(2, {
      content: '整理三款产品的定价与功能矩阵',
      status: 'pending_acceptance',
      artifacts: [{ artifactId: 'artifact-1', name: '定价与功能矩阵.xlsx', url: 'https://example.test/matrix' }],
    }),
  ]);
  mockedService.addTodoInteraction.mockResolvedValue(event(5, { content: '补充价格来源' }));
  mockedService.acceptTodo.mockResolvedValue({ ...todo, status: 'completed' });
});

describe('TodoDetailPage', () => {
  it('以独立页面展示事件、互动和产物视图', async () => {
    render(<TodoDetailPage />);

    expect(await screen.findByRole('heading', { name: 'Q3 竞品分析' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回待办列表' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '待办详情视图' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '事件' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '互动' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '产物 1' })).toBeInTheDocument();
    expect(await screen.findByText('开始采集竞品资料')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /定价与功能矩阵\.xlsx/ })).toHaveAttribute(
      'href',
      'https://example.test/matrix',
    );
  });

  it('切换互动视图并提交互动', async () => {
    render(<TodoDetailPage />);
    await screen.findByText('开始采集竞品资料');

    fireEvent.click(screen.getByRole('button', { name: '互动' }));
    expect(await screen.findByText('请补充价格来源')).toBeInTheDocument();
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
    await screen.findByText('开始采集竞品资料');

    const acceptance = screen.getByRole('region', { name: '待办验收' });
    fireEvent.change(within(acceptance).getByPlaceholderText('可选：填写验收意见'), {
      target: { value: ' 请修改 ' },
    });
    fireEvent.click(within(acceptance).getByRole('button', { name: '验收不通过' }));

    await waitFor(() =>
      expect(mockedService.acceptTodo).toHaveBeenCalledWith('todo-1', {
        verdict: 'rejected',
        message: '请修改',
      }),
    );
    expect(mockedService.startTodoRun).not.toHaveBeenCalled();
  });

  it('产物视图汇总事件中的产物', async () => {
    render(<TodoDetailPage />);
    await screen.findByText('开始采集竞品资料');

    fireEvent.click(screen.getByRole('button', { name: '产物 1' }));
    const artifactView = screen.getByRole('region', { name: '待办产物' });
    expect(within(artifactView).getByRole('link', { name: /定价与功能矩阵\.xlsx/ })).toBeInTheDocument();
  });

  it('返回列表', async () => {
    render(<TodoDetailPage />);
    fireEvent.click(screen.getByRole('button', { name: '返回待办列表' }));
    expect(mockedHistory.push).toHaveBeenCalledWith('/lab/todos');
  });
});
