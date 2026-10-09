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
    await expect(getBotIamToken('bot-1', 'u1', 'owner-1', 'online')).resolves.toBe('bot-token');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/iam-token?user_id=u1&entity_id=owner-1&stage=online',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});
