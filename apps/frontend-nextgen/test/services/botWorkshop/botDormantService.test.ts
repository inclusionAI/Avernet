import { botDormantService } from '@/services/botWorkshop/botDormantService';
import { mapBotDto } from '@/services/bots/botMapper';

const bot = mapBotDto({
  bot_id: 'b1',
  bot_name: '回收 Bot',
  engine: 'openclaw',
  bot_type: 'personal',
  status: 'RECYCLED',
  display_state: 'dormant',
  owner_entity_id: '149608',
  actions: ['view', 'activate', 'delete'],
}).item;
afterEach(() => jest.restoreAllMocks());

test.each([200, 202])('activation accepts HTTP %s and explicitly addresses caller and owner', async (httpStatus) => {
  const result = { bot_id: bot.id, status: httpStatus === 202 ? 'REACTIVATING' : 'ACTIVE' };
  const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    status: httpStatus,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ code: httpStatus * 1000, data: result }),
  } as Response);
  await expect(botDormantService.activate(bot, '149608')).resolves.toEqual(result);
  const [input, init] = fetch.mock.calls[0];
  const url = new URL(String(input), 'http://localhost');
  expect(url.pathname).toBe('/openapi/v1/bots/b1/activate');
  expect(url.searchParams.get('user_id')).toBe('149608');
  expect(url.searchParams.get('owner_id')).toBe('149608');
  expect(init?.method).toBe('POST');
});

test.each([
  { ownerId: 'other' },
  { actions: ['view'] },
  { disabledActions: { activate: '禁止激活' } },
  { serviceMode: 'service' as const },
  { deployment: 'local' as const },
  { runtime: { ...bot.runtime, engine: 'teclaw' } },
  { lifecycle: 'deploying' as const },
])('unsupported or unauthorized activation never sends a request: %j', async (patch) => {
  const fetch = jest.spyOn(globalThis, 'fetch');
  await expect(botDormantService.activate({ ...bot, ...patch }, '149608')).rejects.toThrow();
  expect(fetch).not.toHaveBeenCalled();
});

test('HTTP failure is not treated as accepted activation', async () => {
  jest.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network unavailable'));
  await expect(botDormantService.activate(bot, '149608')).rejects.toThrow();
});

test.each(['RECYCLED', 'FAILED'])('unexpected response %s does not claim success', async (status) => {
  jest.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ code: 200000, data: { bot_id: bot.id, status } }),
  } as Response);
  await expect(botDormantService.activate(bot, '149608')).rejects.toThrow();
});
