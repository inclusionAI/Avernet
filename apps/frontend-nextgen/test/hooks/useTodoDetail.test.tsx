/** @jest-environment jsdom */
import type { TodoEvent, TodoRecord } from '@/domain/todos/models';
import { useTodoDetail } from '@/hooks/useTodoDetail';
import * as todoService from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/todos/todoService');
jest.mock('@/components/ui/notify', () => ({ notifySuccess: jest.fn() }));
jest.mock('@/utils/errorToast', () => ({ safeReportError: jest.fn() }));

const mockedService = jest.mocked(todoService);

const runningTodo: TodoRecord = {
  todoId: 'todo-1',
  spaceId: 10,
  title: '编写方案',
  description: '补齐实施方案',
  botId: 'bot-1',
  botName: '写作助手',
  status: 'in_progress',
  collabContextId: 'ctx-1',
  createdAt: '2026-11-10T01:00:00Z',
  updatedAt: '2026-11-10T02:00:00Z',
};

const awaitingAcceptanceTodo: TodoRecord = { ...runningTodo, status: 'pending_acceptance' };
const pendingDispatchTodo: TodoRecord = { ...runningTodo, status: 'pending', collabContextId: undefined };

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
    payload: {},
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
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
  mockedService.loadTodo.mockResolvedValue(awaitingAcceptanceTodo);
  mockedService.loadTodoInteractions.mockResolvedValue([]);
  mockedService.loadTodoEvents.mockResolvedValue([]);
});

test('B 事件接口失败时不阻断 A 的详情和互动加载', async () => {
  mockedService.loadTodoEvents.mockRejectedValue(new Error('事件服务暂不可用'));

  const { result } = renderHook(() => useTodoDetail('todo-1'));

  await waitFor(() => expect(result.current.selectedTodo).toEqual(awaitingAcceptanceTodo));
  await waitFor(() => expect(result.current.eventsLoading).toBe(false));

  expect(result.current.loading).toBe(false);
  expect(result.current.error).toBeNull();
  expect(result.current.interactions).toEqual([]);
  expect(result.current.events).toEqual([]);
  expect(result.current.eventsError).toBe('事件服务暂不可用');
});

test('事件时序可独立重试且按 eventSeq 排序', async () => {
  mockedService.loadTodoEvents
    .mockRejectedValueOnce(new Error('事件服务暂不可用'))
    .mockResolvedValueOnce([event(2), event(1)]);

  const { result } = renderHook(() => useTodoDetail('todo-1'));
  await waitFor(() => expect(result.current.eventsError).toBe('事件服务暂不可用'));

  await act(async () => {
    await result.current.refreshEvents();
  });

  expect(result.current.eventsError).toBeNull();
  expect(result.current.events.map((item) => item.eventSeq)).toEqual([1, 2]);
});

test('待验收互动只提交 message，不再组装本快照事件', async () => {
  mockedService.loadTodo.mockResolvedValue(awaitingAcceptanceTodo);
  mockedService.addTodoInteraction.mockResolvedValue(event(3, { eventType: 'user_interaction.posted' }));

  const { result } = renderHook(() => useTodoDetail('todo-1'));
  await waitFor(() => expect(result.current.selectedTodo).toEqual(awaitingAcceptanceTodo));

  await act(async () => {
    expect(await result.current.interact(' 补充回滚方案 ')).toBe(true);
  });

  expect(mockedService.addTodoInteraction).toHaveBeenCalledWith('todo-1', { message: '补充回滚方案' });
  expect(mockedService.startTodoRun).not.toHaveBeenCalled();
});

test('拒绝验收提交稳定决策与问题目标，不再调用 Run 接口', async () => {
  mockedService.acceptTodo.mockResolvedValue(awaitingAcceptanceTodo);

  const { result } = renderHook(() => useTodoDetail('todo-1'));
  await waitFor(() => expect(result.current.selectedTodo).toEqual(awaitingAcceptanceTodo));

  await act(async () => {
    expect(
      await result.current.accept({
        decisionId: 'decision-1',
        verdict: 'rejected',
        message: ' 请修改 ',
        targets: [{ kind: 'event', id: 'event-3', floor: 3 }],
      }),
    ).toBe(true);
  });

  expect(mockedService.acceptTodo).toHaveBeenCalledWith('todo-1', {
    decisionId: 'decision-1',
    verdict: 'rejected',
    message: '请修改',
    targets: [{ kind: 'event', id: 'event-3', floor: 3 }],
  });
  expect(mockedService.startTodoRun).not.toHaveBeenCalled();
});

test('待处理 Todo 不暴露普通用户 Run 操作', async () => {
  mockedService.loadTodo.mockResolvedValue(pendingDispatchTodo);

  const { result } = renderHook(() => useTodoDetail('todo-1'));
  await waitFor(() => expect(result.current.selectedTodo).toEqual(pendingDispatchTodo));

  expect('run' in result.current).toBe(false);
  expect(mockedService.startTodoRun).not.toHaveBeenCalled();
});

test('执行中详情打开期间轮询刷新 Todo 和事件', async () => {
  jest.useFakeTimers();
  mockedService.loadTodo.mockResolvedValue(runningTodo);
  mockedService.loadTodoEvents.mockResolvedValue([event(1)]);
  useTodoStore.setState({ selectedTodo: runningTodo });

  const { unmount } = renderHook(() => useTodoDetail('todo-1'));

  await act(async () => {
    jest.advanceTimersByTime(5100);
  });

  expect(mockedService.loadTodo.mock.calls.length).toBeGreaterThanOrEqual(2);
  expect(mockedService.loadTodoEvents.mock.calls.length).toBeGreaterThanOrEqual(2);
  unmount();
  jest.useRealTimers();
});
