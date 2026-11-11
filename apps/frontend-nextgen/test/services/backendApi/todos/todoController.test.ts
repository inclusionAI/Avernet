import { backendRequest } from '@/services/backendApi/httpClient';
import {
  createTodo,
  createTodoInteraction,
  getTodo,
  listTodoEvents,
  listTodoInteractions,
  listTodos,
  runTodo,
  submitTodoAcceptance,
  updateTodo,
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

  it('下发当前用户、空间、状态、负责人和搜索条件', async () => {
    await listTodos({
      spaceId: 10,
      status: 'pending_acceptance',
      botId: 'bot-1',
      keyword: '方案',
      page: 2,
      pageSize: 20,
    });

    expect(request).toHaveBeenCalledWith('/openapi/v1/todos', {
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

    expect(request).toHaveBeenCalledWith('/openapi/v1/todos/todo%2F1', {
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

    expect(request).toHaveBeenCalledWith('/openapi/v1/todos', {
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

  it('更新待办时下发当前用户', async () => {
    await updateTodo('todo/1', {
      title: '新标题',
      description: '新描述',
      botId: 'bot-2',
    });

    expect(request).toHaveBeenCalledWith('/openapi/v1/todos/todo%2F1', {
      method: 'PATCH',
      params: userParams,
      data: {
        title: '新标题',
        description: '新描述',
        bot_id: 'bot-2',
      },
      operation: 'update-todo',
    });
  });

  it('提交验收时下发当前用户', async () => {
    await submitTodoAcceptance('todo/1', {
      verdict: 'approved',
      message: '通过',
    });

    expect(request).toHaveBeenCalledWith('/openapi/v1/todos/todo%2F1/acceptance', {
      method: 'POST',
      params: userParams,
      data: {
        verdict: 'approved',
        message: '通过',
      },
      operation: 'accept-todo',
    });
  });

  it('查询互动时下发当前用户', async () => {
    await listTodoInteractions('todo/1');

    expect(request).toHaveBeenCalledWith('/openapi/v1/todos/todo%2F1/interactions', {
      method: 'GET',
      params: userParams,
      operation: 'list-todo-interactions',
      retryOnTransient: true,
    });
  });

  it('创建互动时下发当前用户', async () => {
    await createTodoInteraction('todo/1', { message: '补充信息' });

    expect(request).toHaveBeenCalledWith('/openapi/v1/todos/todo%2F1/interactions', {
      method: 'POST',
      params: userParams,
      data: { message: '补充信息' },
      operation: 'create-todo-interaction',
    });
  });

  it('查询事件时下发当前用户', async () => {
    await listTodoEvents('todo/1');

    expect(request).toHaveBeenCalledWith('/openapi/v1/todos/todo%2F1/events', {
      method: 'GET',
      params: userParams,
      operation: 'list-todo-events',
      retryOnTransient: true,
    });
  });

  it('运行待办时下发当前用户', async () => {
    await runTodo('todo/1');

    expect(request).toHaveBeenCalledWith('/openapi/v1/todos/todo%2F1/run', {
      method: 'POST',
      params: userParams,
      operation: 'run-todo',
    });
  });
});
