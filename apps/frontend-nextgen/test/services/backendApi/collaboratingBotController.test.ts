import { listCollaboratingBots } from '@/services/backendApi/bots/collaboratingBotController';
import { backendRequest } from '@/services/backendApi/httpClient';
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = jest.mocked(backendRequest);
beforeEach(() => request.mockReset());

test('collaborations 只发送登录 user_id 与分页，不注入工作身份', async () => {
  const signal = new AbortController().signal;
  const response = { code: 200000, data: { items: [], total: 0 } };
  request.mockResolvedValue(response);
  await expect(listCollaboratingBots({ user_id: 'viewer', page: 2, page_size: 100 }, signal)).resolves.toEqual(
    response,
  );
  expect(request).toHaveBeenCalledWith('/openapi/v1/bots/collaborations', {
    method: 'GET',
    params: { user_id: 'viewer', page: 2, page_size: 100 },
    injectUserId: false,
    signal,
  });
});

test('collaborations forwards the online stage filter without injecting identity', async () => {
  const params = { user_id: 'viewer', page: 1, page_size: 100, stage: 'online' as const };
  await listCollaboratingBots(params);
  expect(request).toHaveBeenCalledWith('/openapi/v1/bots/collaborations', {
    method: 'GET',
    params,
    injectUserId: false,
    signal: undefined,
  });
});
