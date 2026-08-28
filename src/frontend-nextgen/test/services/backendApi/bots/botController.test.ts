import {
  changeBotSpace,
  deleteBot,
  listBotInventory,
  restartBot,
  restartBotEngine,
  upgradeBotToService,
} from '@/services/backendApi/bots/botController';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { afterEach, describe, expect, jest, test } from '@jest/globals';

const response = (data: unknown = null) =>
  Promise.resolve({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ code: 200000, message: 'OK', data, request_id: 'trace' }),
  } as Response);

describe('botController OpenAPI contracts', () => {
  afterEach(() => {
    jest.restoreAllMocks();
    useWorkspaceStore.setState({ activeIdentityId: null });
  });
  test('adds the current OpenAPI user id to inventory requests', async () => {
    useWorkspaceStore.setState({ activeIdentityId: 'human_327325' });
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response({ total: 0, items: [] }));

    await listBotInventory({ page: 1, page_size: 20 });

    expect(spy).toHaveBeenCalledWith(
      '/openapi/v1/bots/all?page=1&page_size=20&user_id=327325',
      expect.objectContaining({ method: 'GET' }),
    );
  });
  test('lists unified inventory with space header and OpenAPI query names', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response({ total: 0, items: [] }));
    await listBotInventory({ page: 2, page_size: 10, deploy_mode: 'cloud' }, 'space-a');
    expect(spy).toHaveBeenCalledWith(
      '/openapi/v1/bots/all?page=2&page_size=10&deploy_mode=cloud',
      expect.objectContaining({ method: 'GET', headers: expect.objectContaining({ 'X-Space-Id': 'space-a' }) }),
    );
  });
  test('passes service classification to the paginated inventory API', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response({ total: 0, items: [] }));
    await listBotInventory({ page: 1, page_size: 20, is_service: false });
    expect(spy).toHaveBeenCalledWith(
      '/openapi/v1/bots/all?page=1&page_size=20&is_service=false',
      expect.objectContaining({ method: 'GET' }),
    );
  });
  test.each([
    ['change space', () => changeBotSpace('bot-1', 12, 'u1'), '/openapi/v1/bots/bot-1/space?user_id=u1', 'PUT'],
    ['delete', () => deleteBot('bot-1'), '/openapi/v1/bots/bot-1', 'DELETE'],
    ['restart', () => restartBot('bot-1'), '/openapi/v1/bots/bot-1/restart', 'POST'],
    ['restart engine', () => restartBotEngine('bot-1'), '/openapi/v1/bots/bot-1/engine/restart', 'POST'],
    ['upgrade service', () => upgradeBotToService('bot-1'), '/openapi/v1/bots/bot-1/lifecycle/upgrade', 'POST'],
  ])('%s uses only OpenAPI', async (_name, invoke, url, method) => {
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response());
    await invoke();
    expect(spy).toHaveBeenCalledWith(url, expect.objectContaining({ method }));
  });
});
