import {
  createBotSession,
  getBotConnection,
  listBotSessionMessages,
  listBotSessions,
} from '@/services/backendApi/bots/privateBotSessionController';
import { afterEach, describe, expect, jest, test } from '@jest/globals';

const ok = () =>
  Promise.resolve({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ code: 200000, data: { items: [] } }),
  } as Response);

describe('privateBotSessionController canonical OpenAPI', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });
  test.each([
    ['list', () => listBotSessions('bot-1', { user_id: 'u1' }), '/openapi/v1/bots/bot-1/sessions?user_id=u1', 'GET'],
    [
      'create',
      () => createBotSession('bot-1', { user_id: 'u1' }, { title: 'debug' }),
      '/openapi/v1/bots/bot-1/sessions?user_id=u1',
      'POST',
    ],
    [
      'messages',
      () => listBotSessionMessages('bot-1', 's1', { user_id: 'u1' }),
      '/openapi/v1/bots/bot-1/sessions/s1/messages?user_id=u1',
      'GET',
    ],
    [
      'connection',
      () => getBotConnection('bot-1', { user_id: 'u1' }),
      '/openapi/v1/bots/bot-1/connection?user_id=u1',
      'GET',
    ],
  ])('%s uses the canonical bot-addressed route', async (_label, invoke, url, method) => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    await invoke();
    expect(fetch).toHaveBeenCalledWith(url, expect.objectContaining({ method }));
  });
});
