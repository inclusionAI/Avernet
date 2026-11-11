import { listTodos } from '@/services/backendApi/todos/todoController';
import { backendRequest } from '@/services/backendApi/httpClient';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));

const request = jest.mocked(backendRequest);

describe('todoController', () => {
  beforeEach(() => {
    request.mockReset();
    request.mockResolvedValue({ code: 200000, data: { items: [], total: 0 } } as never);
  });

  it('下发当前空间、状态、负责人和搜索条件', async () => {
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
      },
      operation: 'list-todos',
      retryOnTransient: true,
    });
  });
});
