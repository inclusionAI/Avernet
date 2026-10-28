import {
  changeBotSpace,
  deleteBot,
  getBot,
  getBotEngineStatus,
  getBotEngineVersion,
  getBotRestartStatus,
  listBotInventory,
  listBots,
  refetchBotEngine,
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
  test('lists owned Bot engines with the explicit user id and pagination contract', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response({ total: 1, items: [] }));

    await listBots({ user_id: '900004', page: 1, page_size: 100 });

    expect(spy).toHaveBeenCalledWith(
      '/openapi/v1/bots?user_id=900004&page=1&page_size=100',
      expect.objectContaining({ method: 'GET' }),
    );
  });

  test('adds the current OpenAPI user id to inventory requests', async () => {
    useWorkspaceStore.setState({ activeIdentityId: 'human_900003' });
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response({ total: 0, items: [] }));

    await listBotInventory({ page: 1, page_size: 20 });

    expect(spy).toHaveBeenCalledWith(
      '/openapi/v1/bots/all?page=1&page_size=20&user_id=900003',
      expect.objectContaining({ method: 'GET' }),
    );
  });
  test('inventory 保留团队空间及调用方取消信号', async () => {
    const controller = new AbortController();
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response({ total: 0, items: [] }));
    await listBotInventory({ user_id: 'viewer', page: 1, page_size: 100 }, '42', controller.signal);
    expect(spy).toHaveBeenCalledWith(
      '/openapi/v1/bots/all?user_id=viewer&page=1&page_size=100',
      expect.objectContaining({ signal: controller.signal, headers: expect.objectContaining({ 'X-Space-Id': '42' }) }),
    );
  });

  test('does not send placeholder identity me as user_id', async () => {
    useWorkspaceStore.setState({ activeIdentityId: 'me' });
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response({ total: 0, items: [] }));

    await listBotInventory({ page: 1, page_size: 20 });

    expect(spy).toHaveBeenCalledWith(
      '/openapi/v1/bots/all?page=1&page_size=20',
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
  test('refetch engine uses the Coding refetch contract and force semantics', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response());
    await refetchBotEngine('bot-1');
    expect(spy).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/engine/refetch',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ force: true }) }),
    );
  });

  test('engine-version inspection omits the default draft stage and preserves instance identity', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response({ comparison: 'older' }));

    await getBotEngineVersion('bot-1', {
      userId: '900001',
      ownerId: 'owner-1',
      stage: 'draft',
      deviceUuid: 'device-1',
    });

    expect(spy).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/engine-version?user_id=900001&entity_id=owner-1&device_uuid=device-1',
      expect.objectContaining({ method: 'GET' }),
    );
  });

  test('engine status and restart omit the default draft stage and preserve instance identity', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response({ running: true }));
    const target = { userId: '900001', ownerId: 'owner-1', deviceUuid: 'device-1', stage: 'draft' as const };

    await getBotEngineStatus('bot-1', target);
    await restartBotEngine('bot-1', target);

    expect(spy).toHaveBeenNthCalledWith(
      1,
      '/openapi/v1/bots/bot-1/engine/status?user_id=900001&entity_id=owner-1&device_uuid=device-1',
      expect.objectContaining({ method: 'GET' }),
    );
    expect(spy).toHaveBeenNthCalledWith(
      2,
      '/openapi/v1/bots/bot-1/engine/restart?user_id=900001&entity_id=owner-1&device_uuid=device-1',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ force: false }) }),
    );
  });

  test('Bot restart and lifecycle poll preserve caller/owner without stale device or engine-process semantics', async () => {
    const spy = jest.spyOn(globalThis, 'fetch').mockImplementation(() => response({ status: 'PENDING' }));
    const controller = new AbortController();
    const target = { userId: 'caller', ownerId: 'owner', deviceUuid: 'old-device', signal: controller.signal };
    await restartBot('bot-1', target);
    await getBotRestartStatus('bot-1', target);
    expect(spy).toHaveBeenNthCalledWith(
      1,
      '/openapi/v1/bots/bot-1/restart?user_id=caller&entity_id=owner',
      expect.objectContaining({ method: 'POST', signal: controller.signal }),
    );
    expect(spy).toHaveBeenNthCalledWith(
      2,
      '/openapi/v1/bots/bot-1/status?user_id=caller&entity_id=owner',
      expect.objectContaining({ method: 'GET', signal: controller.signal }),
    );
  });

  test.each([
    ['detail as collaborator', () => getBot('bot-1', 'owner-1'), '/openapi/v1/bots/bot-1?owner_id=owner-1', 'GET'],
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
