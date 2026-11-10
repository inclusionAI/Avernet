import { getBotIamToken, getIamToken } from '@/services/backendApi/privateChat/iamTokenController';
import { afterEach, describe, expect, jest, test } from '@jest/globals';

describe('iamTokenController OpenAPI contract', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });
  test('uses OpenAPI and returns the enveloped token', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, message: 'OK', data: { iam_token: 'token-1' } }),
    } as Response);
    await expect(getIamToken('u1')).resolves.toBe('token-1');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/org/user/iam-token?user_id=u1',
      expect.objectContaining({ method: 'GET' }),
    );
  });
  test('uses Bot-scoped IAM token endpoint for single chat', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, message: 'OK', data: { iam_token: 'bot-token' } }),
    } as Response);
    await expect(
      getBotIamToken('bot-1', 'u1', 'owner-1', 'online', { sessionKey: 'online-session' }),
    ).resolves.toBe('bot-token');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/iam-token?user_id=u1&entity_id=owner-1&stage=online&session_key=online-session',
      expect.objectContaining({ method: 'POST' }),
    );
  });
  test('uses the verified friend session parameters for an online friend Bot', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, message: 'OK', data: { iam_token: 'test-token' } }),
    } as Response);
    await expect(
      getBotIamToken('bot-1', 'u1', 'entity-1', 'online', {
        sessionKey: 'friend-session',
        friendUserId: 'u1',
        ownerId: 'owner-1',
      }),
    ).resolves.toBe('test-token');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/iam-token?user_id=u1&entity_id=entity-1&stage=online&owner_id=owner-1&f_user_id=u1&session_id=friend-session',
      expect.objectContaining({ method: 'POST' }),
    );
  });
  test('omits stage when no runtime override is needed', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, data: { iam_token: 'token' } }),
    } as Response);
    await getBotIamToken('personal', 'viewer', 'owner');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/personal/iam-token?user_id=viewer&entity_id=owner',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});
