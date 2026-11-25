/** @jest-environment jsdom */
import type { TodoArtifact, TodoEvent, TodoRecord } from '@/domain/todos/models';
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

function createDeferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

function event(eventSeq: number, overrides: Partial<TodoEvent> = {}): TodoEvent {
  return {
    eventId: `event-${eventSeq}`,
    eventSeq,
    eventType: 'execution.status',
    actorId: 'bot-1',
    actorType: 'bot',
    content: `事件 ${eventSeq}`,
    executionId: 'execution-1',
    isLatest: true,
    replyable: true,
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
    artifacts: [],
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
  mockedService.loadTodoArtifacts.mockResolvedValue([]);
  mockedService.loadTodoMentionBots.mockResolvedValue([]);
});

test('切换待办时立即清理旧详情，避免先渲染上一个待办', async () => {
  const firstTodo: TodoRecord = { ...runningTodo, todoId: 'todo-1', title: '第一个待办' };
  const secondTodo: TodoRecord = { ...runningTodo, todoId: 'todo-2', title: '第二个待办' };
  const firstArtifact: TodoArtifact = {
    artifactId: 'artifact-1',
    todoId: 'todo-1',
    executionId: 'execution-1',
    artifactName: '第一个产物',
    kind: 'text',
    content: { text: '第一个待办的产物', isFinalArtifact: true },
    createdAt: '2026-11-10T01:30:00Z',
  };
  mockedService.loadTodo.mockResolvedValueOnce(firstTodo);
  mockedService.loadTodoInteractions.mockResolvedValueOnce([event(1)]);
  mockedService.loadTodoEvents.mockResolvedValueOnce([event(1)]);
  mockedService.loadTodoArtifacts.mockResolvedValueOnce([firstArtifact]);

  const { result, rerender } = renderHook((todoId) => useTodoDetail(todoId), {
    initialProps: 'todo-1',
  });
  await waitFor(() => {
    expect(result.current.selectedTodo).toEqual(firstTodo);
    expect(result.current.interactions).toHaveLength(1);
    expect(result.current.events).toHaveLength(1);
    expect(result.current.artifacts).toEqual([firstArtifact]);
  });

  const secondDetail = createDeferred<TodoRecord>();
  const secondInteractions = createDeferred<TodoEvent[]>();
  const secondEvents = createDeferred<TodoEvent[]>();
  const secondArtifacts = createDeferred<TodoArtifact[]>();
  mockedService.loadTodo.mockResolvedValueOnce(secondDetail.promise);
  mockedService.loadTodoInteractions.mockResolvedValueOnce(secondInteractions.promise);
  mockedService.loadTodoEvents.mockResolvedValueOnce(secondEvents.promise);
  mockedService.loadTodoArtifacts.mockResolvedValueOnce(secondArtifacts.promise);

  rerender('todo-2');

  expect(result.current.selectedTodo).toBeNull();
  expect(result.current.interactions).toEqual([]);
  expect(result.current.events).toEqual([]);
  expect(result.current.artifacts).toEqual([]);
  expect(result.current.loading).toBe(true);

  await act(async () => {
    secondDetail.resolve(secondTodo);
    secondInteractions.resolve([event(2, { eventId: 'event-2' })]);
    secondEvents.resolve([event(2, { eventId: 'event-2' })]);
    secondArtifacts.resolve([
      {
        ...firstArtifact,
        artifactId: 'artifact-2',
        todoId: 'todo-2',
        artifactName: '第二个产物',
        content: { text: '第二个待办的产物', isFinalArtifact: true },
      },
    ]);
    await Promise.all([
      secondDetail.promise,
      secondInteractions.promise,
      secondEvents.promise,
      secondArtifacts.promise,
    ]);
  });

  expect(result.current.selectedTodo).toEqual(secondTodo);
  expect(result.current.loading).toBe(false);
  expect(result.current.artifacts[0]?.artifactId).toBe('artifact-2');
  expect(mockedService.loadTodo).toHaveBeenLastCalledWith('todo-2');
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

test('事件时序可独立重试并保留服务端跨 Context 顺序', async () => {
  mockedService.loadTodoEvents
    .mockRejectedValueOnce(new Error('事件服务暂不可用'))
    .mockResolvedValueOnce([event(2), event(1)]);

  const { result } = renderHook(() => useTodoDetail('todo-1'));
  await waitFor(() => expect(result.current.eventsError).toBe('事件服务暂不可用'));

  await act(async () => {
    await result.current.refreshEvents();
  });

  expect(result.current.eventsError).toBeNull();
  expect(result.current.events.map((item) => item.eventSeq)).toEqual([2, 1]);
});

test('待验收互动保留稳定回复和结构化 mention，只修剪 message', async () => {
  mockedService.loadTodo.mockResolvedValue(awaitingAcceptanceTodo);
  mockedService.addTodoInteraction.mockResolvedValue(event(3, { eventType: 'user_interaction.posted' }));

  const { result } = renderHook(() => useTodoDetail('todo-1'));
  await waitFor(() => expect(result.current.selectedTodo).toEqual(awaitingAcceptanceTodo));

  await act(async () => {
    expect(
      await result.current.interact({
        message: ' @复核助手 补充回滚方案 ',
        replyToEventId: 'event-2',
        mentions: [{ actorType: 'bot', actorId: 'bot-review' }],
      }),
    ).toBe(true);
  });

  expect(mockedService.addTodoInteraction).toHaveBeenCalledWith('todo-1', {
    message: '@复核助手 补充回滚方案',
    replyToEventId: 'event-2',
    mentions: [{ actorType: 'bot', actorId: 'bot-review' }],
  });
  expect(mockedService.startTodoRun).not.toHaveBeenCalled();
});

test('已完成模板 Todo 可针对最新节点提交重跑互动', async () => {
  const completedTemplateTodo: TodoRecord = {
    ...runningTodo,
    templateId: 'template-1',
    status: 'completed',
  };
  mockedService.loadTodo.mockResolvedValue(completedTemplateTodo);
  mockedService.addTodoInteraction.mockResolvedValue(event(3, { eventType: 'user_interaction.posted' }));

  const { result } = renderHook(() => useTodoDetail('todo-1'));
  await waitFor(() => expect(result.current.selectedTodo).toEqual(completedTemplateTodo));

  await act(async () => {
    expect(
      await result.current.interact({
        message: ' 从第三个节点重跑 ',
        replyToEventId: 'event-stage-3',
        targetExecutionId: 'todoexec-stage-3-v1',
        targetStageInstanceId: 'stage-3',
      }),
    ).toBe(true);
  });

  expect(mockedService.addTodoInteraction).toHaveBeenCalledWith('todo-1', {
    message: '从第三个节点重跑',
    replyToEventId: 'event-stage-3',
    targetExecutionId: 'todoexec-stage-3-v1',
    targetStageInstanceId: 'stage-3',
  });
});

test('模板 Todo 缺少节点版本目标时拒绝提交', async () => {
  const completedTemplateTodo: TodoRecord = {
    ...runningTodo,
    templateId: 'template-1',
    status: 'completed',
  };
  mockedService.loadTodo.mockResolvedValue(completedTemplateTodo);

  const { result } = renderHook(() => useTodoDetail('todo-1'));
  await waitFor(() => expect(result.current.selectedTodo).toEqual(completedTemplateTodo));

  await act(async () => {
    expect(
      await result.current.interact({
        message: '从第三个节点重跑',
        replyToEventId: 'event-stage-3',
      }),
    ).toBe(false);
  });

  expect(mockedService.addTodoInteraction).not.toHaveBeenCalled();
});

test('空间 Bot 候选加载失败不阻断待办详情和互动历史', async () => {
  mockedService.loadTodoInteractions.mockResolvedValue([event(1)]);
  mockedService.loadTodoMentionBots.mockRejectedValue(new Error('Bot 目录暂不可用'));

  const { result } = renderHook(() => useTodoDetail('todo-1'));

  await waitFor(() => expect(result.current.selectedTodo).toEqual(awaitingAcceptanceTodo));
  await waitFor(() => expect(result.current.mentionBotsLoading).toBe(false));

  expect(mockedService.loadTodoMentionBots).toHaveBeenCalledWith(10);
  expect(result.current.interactions).toHaveLength(1);
  expect(result.current.mentionBots).toEqual([]);
  expect(result.current.mentionBotsError).toBe('Bot 目录暂不可用');
  expect(result.current.error).toBeNull();
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

  const { result, unmount } = renderHook(() => useTodoDetail('todo-1'));
  await act(async () => {
    await Promise.resolve();
  });
  expect(result.current.loading).toBe(false);

  await act(async () => {
    jest.advanceTimersByTime(5100);
  });

  expect(mockedService.loadTodo.mock.calls.length).toBeGreaterThanOrEqual(2);
  expect(mockedService.loadTodoEvents.mock.calls.length).toBeGreaterThanOrEqual(2);
  unmount();
  jest.useRealTimers();
});
