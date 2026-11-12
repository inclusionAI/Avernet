import { listTodos } from '@/services/backendApi/todos/todoController';

const originalFetch = globalThis.fetch;
const configuredUser = Object.getOwnPropertyDescriptor(globalThis, 'TEAMCLAW_OPENAPI_USER_ID');

beforeEach(() => {
  Object.defineProperty(globalThis, 'TEAMCLAW_OPENAPI_USER_ID', { configurable: true, value: '149608' });
  globalThis.fetch = jest.fn(async () => ({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ code: 200000, message: 'OK', data: { items: [], total: 0, page: 1, pageSize: 10 } }),
  } as Response));
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (configuredUser) Object.defineProperty(globalThis, 'TEAMCLAW_OPENAPI_USER_ID', configuredUser);
  else Reflect.deleteProperty(globalThis, 'TEAMCLAW_OPENAPI_USER_ID');
});

it('全量查询经过真实 userScopedParams 与 HTTP 序列化时不携带 status', async () => {
  await expect(
    listTodos({
      spaceId: 10,
      keyword: '编写',
      botId: 'bot-owned:u-1',
      page: 1,
      pageSize: 10,
    }),
  ).resolves.toEqual({
    code: 200000,
    message: 'OK',
    data: { items: [], total: 0, page: 1, pageSize: 10 },
  });

  expect(jest.mocked(globalThis.fetch)).toHaveBeenCalledTimes(1);
  const requestUrl = new URL(jest.mocked(globalThis.fetch).mock.calls[0][0] as string, 'http://localhost');
  expect(requestUrl.pathname).toBe('/openapi/v1/todos');
  expect(requestUrl.searchParams.has('status')).toBe(false);
  expect(requestUrl.searchParams.get('keyword')).toBe('编写');
  expect(requestUrl.searchParams.get('bot_id')).toBe('bot-owned:u-1');
  expect(requestUrl.searchParams.get('space_id')).toBe('10');
  expect(requestUrl.searchParams.get('user_id')).toBe('149608');
  expect(requestUrl.searchParams.get('page')).toBe('1');
  expect(requestUrl.searchParams.get('page_size')).toBe('10');
});
