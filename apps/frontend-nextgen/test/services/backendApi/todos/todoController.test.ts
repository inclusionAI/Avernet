import { backendRequest } from '@/services/backendApi/httpClient';
import {
  createTodo,
  createTodoInteraction,
  getTodo,
  getTodoSourceDraft,
  listTodoEvents,
  listTodoInteractions,
  listTodos,
  listTodoSourceProjects,
  listTodoSourceWorkItems,
  runTodo,
  submitTodoAcceptance,
} from '@/services/backendApi/todos/todoController';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
jest.mock('@/services/backendApi/bots/botController', () => ({
  userScopedParams: jest.fn((params: Record<string, unknown> = {}) => ({
    ...params,
    user_id: 'human-1',
  })),
}));

const request = jest.mocked(backendRequest);

const userParams = { user_id: 'human-1' };

describe('todoController', () => {
  beforeEach(() => {
    request.mockReset();
    request.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } } as never);
  });

  it('全量查询不下发空筛选键', async () => {
    await listTodos({
      spaceId: 10,
      page: 1,
      pageSize: 10,
    });

    expect(request).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/todos', {
      method: 'GET',
      params: {
        space_id: 10,
        page: 1,
        page_size: 10,
        user_id: 'human-1',
      },
      operation: 'list-todos',
      retryOnTransient: true,
    });
  });

  it('下发当前用户、空间、状态、负责人和搜索条件', async () => {
    await listTodos({
      spaceId: 10,
      status: 'pending_acceptance',
      botId: 'bot-1',
      keyword: '方案',
      page: 2,
      pageSize: 20,
    });

    expect(request).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/todos', {
      method: 'GET',
      params: {
        space_id: 10,
        keyword: '方案',
        status: 'pending_acceptance',
        bot_id: 'bot-1',
        page: 2,
        page_size: 20,
        user_id: 'human-1',
      },
      operation: 'list-todos',
      retryOnTransient: true,
    });
  });

  it('查询待办详情时下发当前用户', async () => {
    await getTodo('todo/1');

    expect(request).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/todos/todo%2F1', {
      method: 'GET',
      params: userParams,
      operation: 'get-todo',
      retryOnTransient: true,
    });
  });

  it('创建待办时下发当前用户', async () => {
    await createTodo({
      spaceId: 10,
      title: '标题',
      description: '描述',
      botId: 'bot-1',
    });

    expect(request).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/todos', {
      method: 'POST',
      params: userParams,
      data: {
        space_id: 10,
        title: '标题',
        description: '描述',
        bot_id: 'bot-1',
      },
      operation: 'create-todo',
    });
  });

  it('创建 Dima 待办时序列化来源身份', async () => {
    await createTodo({
      spaceId: 10,
      title: '标题',
      description: '描述',
      botId: 'bot-1',
      source: {
        type: 'dima',
        containerId: 'P1',
        itemId: 'work-1',
        url: 'https://dima.example/items/work-1',
        updatedAt: '2026-11-20T12:00:00Z',
      },
    });

    expect(request).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/todos', {
      method: 'POST',
      params: userParams,
      data: {
        space_id: 10,
        title: '标题',
        description: '描述',
        bot_id: 'bot-1',
        source: {
          type: 'dima',
          container_id: 'P1',
          item_id: 'work-1',
          url: 'https://dima.example/items/work-1',
          updated_at: '2026-11-20T12:00:00Z',
        },
      },
      operation: 'create-todo',
    });
  });

  it('查询 Dima 来源时编码路径并下发当前用户', async () => {
    await listTodoSourceProjects();
    expect(request).toHaveBeenLastCalledWith('/openapi/v1/collaboration/tasks/todos/sources/dima/projects', {
      method: 'GET',
      params: userParams,
      operation: 'list-todo-source-projects',
      retryOnTransient: true,
    });

    await listTodoSourceWorkItems('P/1', '  支付  ');
    expect(request).toHaveBeenLastCalledWith(
      '/openapi/v1/collaboration/tasks/todos/sources/dima/projects/P%2F1/work-items',
      {
        method: 'GET',
        params: { query: '支付', user_id: 'human-1' },
        operation: 'list-todo-source-work-items',
        retryOnTransient: true,
      },
    );

    await getTodoSourceDraft('P/1', 'work/1');
    expect(request).toHaveBeenLastCalledWith(
      '/openapi/v1/collaboration/tasks/todos/sources/dima/projects/P%2F1/work-items/work%2F1',
      {
        method: 'GET',
        params: userParams,
        operation: 'get-todo-source-draft',
        retryOnTransient: true,
      },
    );
  });

  it('Dima 工作项空搜索不下发 query', async () => {
    await listTodoSourceWorkItems('P1', '   ');

    expect(request).toHaveBeenCalledWith(
      '/openapi/v1/collaboration/tasks/todos/sources/dima/projects/P1/work-items',
      expect.objectContaining({ params: userParams }),
    );
  });

  it('提交验收时下发当前用户', async () => {
    await submitTodoAcceptance('todo/1', {
      decisionId: 'decision-1',
      verdict: 'approved',
      message: '通过',
      targets: [],
    });

    expect(request).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/todos/todo%2F1/acceptance', {
      method: 'POST',
      params: userParams,
      data: {
        decision_id: 'decision-1',
        verdict: 'approved',
        message: '通过',
        targets: [],
      },
      operation: 'accept-todo',
    });
  });

  it('查询互动时下发当前用户', async () => {
    await listTodoInteractions('todo/1');

    expect(request).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/todos/todo%2F1/interactions', {
      method: 'GET',
      params: userParams,
      operation: 'list-todo-interactions',
      retryOnTransient: true,
    });
  });

  it('创建互动时下发当前用户', async () => {
    await createTodoInteraction('todo/1', { message: '补充信息' });

    expect(request).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/todos/todo%2F1/interactions', {
      method: 'POST',
      params: userParams,
      data: { message: '补充信息' },
      operation: 'create-todo-interaction',
    });
  });

  it('创建楼层回复时映射稳定事件和结构化 Bot mention', async () => {
    await createTodoInteraction('todo/1', {
      message: '@复核助手 请确认价格来源',
      replyToEventId: 'event-3',
      targetExecutionId: 'todoexec-stage-3-v1',
      targetStageInstanceId: 'stage-3',
      mentions: [{ actorType: 'bot', actorId: 'bot-review' }],
    });

    expect(request).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/todos/todo%2F1/interactions', {
      method: 'POST',
      params: userParams,
      data: {
        message: '@复核助手 请确认价格来源',
        reply_to_event_id: 'event-3',
        target_execution_id: 'todoexec-stage-3-v1',
        target_stage_instance_id: 'stage-3',
        mentions: [{ actor_type: 'bot', actor_id: 'bot-review' }],
      },
      operation: 'create-todo-interaction',
    });
  });

  it('查询事件时下发当前用户', async () => {
    await listTodoEvents('todo/1');

    expect(request).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/todos/todo%2F1/events', {
      method: 'GET',
      params: userParams,
      operation: 'list-todo-events',
      retryOnTransient: true,
    });
  });

  it('运行待办时下发当前用户', async () => {
    await runTodo('todo/1');

    expect(request).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/todos/todo%2F1/run', {
      method: 'POST',
      params: userParams,
      operation: 'run-todo',
    });
  });
});
