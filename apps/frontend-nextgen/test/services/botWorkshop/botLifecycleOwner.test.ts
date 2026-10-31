import { botEditorService } from '@/services/botWorkshop/botEditorService';

const configuredUser = Object.getOwnPropertyDescriptor(globalThis, 'TEAMCLAW_OPENAPI_USER_ID');
beforeEach(() => {
  Object.defineProperty(globalThis, 'TEAMCLAW_OPENAPI_USER_ID', { configurable: true, value: '149608' });
});
afterEach(() => {
  jest.restoreAllMocks();
  if (configuredUser) Object.defineProperty(globalThis, 'TEAMCLAW_OPENAPI_USER_ID', configuredUser);
  else Reflect.deleteProperty(globalThis, 'TEAMCLAW_OPENAPI_USER_ID');
});

test.each([
  ['query', () => botEditorService.listLifecycle('bot-1', 'owner-1'), '', 'GET'],
  ['upgrade', () => botEditorService.upgradeLifecycle('bot-1', 17, 'owner-1'), '/17/upgrade', 'POST'],
  ['advance', () => botEditorService.advanceLifecycle('bot-1', 'prestable', 'owner-1'), '/advance', 'POST'],
  ['restart', () => botEditorService.restartLifecycle('bot-1', 'online', 'owner-1'), '/restart', 'POST'],
  ['cancel', () => botEditorService.cancelStaging('bot-1', 'owner-1'), '/cancel-staging', 'POST'],
  ['offline', () => botEditorService.offlineLifecycle('bot-1', 'owner-1'), '/offline', 'POST'],
  ['retry', () => botEditorService.retryLifecycle('bot-1', 'owner-1'), '/retry', 'POST'],
  ['delete', () => botEditorService.deleteLifecycleDraft('bot-1', 'owner-1'), '', 'DELETE'],
] as const)('%s passes the addressed owner through Service and Controller to HTTP', async (_, invoke, suffix, method) => {
  const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ code: 200000, data: { items: [] } }),
  } as Response);
  await invoke();
  const [input, init] = fetch.mock.calls[0];
  const url = new URL(String(input), 'http://localhost');
  expect(url.pathname).toBe(`/openapi/v1/bots/bot-1/lifecycle${suffix}`);
  expect(url.searchParams.get('owner_id')).toBe('owner-1');
  expect(url.searchParams.get('user_id')).toBe('149608');
  expect(init?.method).toBe(method);
  const expectedBody = suffix === '/advance' ? { stage: 'prestable' } : suffix === '/restart' ? { stage: 'online' } : undefined;
  expect(init?.body ? JSON.parse(String(init.body)) : undefined).toEqual(expectedBody);
});
