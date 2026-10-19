/** @jest-environment node */
import { queryIsServiceBot } from '@/services/workspace/botClassificationService';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { afterEach, expect, it, jest } from '@jest/globals';

function response(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    headers: new Headers({ 'content-type': 'application/json' }),
  } as Response;
}
afterEach(() => {
  jest.restoreAllMocks();
  useErrorNotifyStore.getState().reset();
});

it('uses the authenticated minimal classification API and encodes composite identity fields', async () => {
  const fetchMock = jest.spyOn(globalThis, 'fetch').mockResolvedValue(
    response({
      code: 200000,
      data: { bot_id: 'bot/1', bot_type: 'service' },
    }),
  );
  expect(await queryIsServiceBot('bot/1:entity/2')).toBe(true);
  expect(fetchMock).toHaveBeenCalledWith(
    '/openapi/v1/bots/bot%2F1/classification?entity_id=entity%2F2',
    expect.objectContaining({ method: 'GET', credentials: 'include' }),
  );
});

it('skips humans and default bots', async () => {
  const fetchMock = jest.spyOn(globalThis, 'fetch');
  expect(await queryIsServiceBot('human_1')).toBe(false);
  expect(await queryIsServiceBot('default:1')).toBe(false);
  expect(fetchMock).not.toHaveBeenCalled();
});

it.each(['personal', 'desktop'])('does not classify %s as service', async (bot_type) => {
  jest.spyOn(globalThis, 'fetch').mockResolvedValue(response({ code: 200000, data: { bot_type } }));
  expect(await queryIsServiceBot('bot')).toBe(false);
});

it('treats absent unmanaged bots as non-service, but rejects ambiguous and failed queries', async () => {
  jest
    .spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(response({ code: 404000, message: 'Not found', data: null }, 404))
    .mockResolvedValueOnce(response({ code: 409000, message: 'ambiguous', data: null }, 409))
    .mockResolvedValueOnce(response({ code: 500000, message: 'failed', data: null }, 500));
  expect(await queryIsServiceBot('absent')).toBe(false);
  expect(useErrorNotifyStore.getState().queue).toEqual([expect.objectContaining({ cancelled: true })]);
  await expect(queryIsServiceBot('ambiguous')).rejects.toThrow('ambiguous');
  await expect(queryIsServiceBot('failed')).rejects.toThrow('failed');
});
