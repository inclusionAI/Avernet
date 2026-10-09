import { botCollaborationController } from '@/services/backendApi/bots/botCollaborationController';
import { afterEach, describe, expect, jest, test } from '@jest/globals';

const ok = () =>
  Promise.resolve({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ code: 200000, data: { auto_approve: false } }),
  } as Response);

describe('botCollaborationController editor request policy', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  test('queries the Bot editor request policy', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);

    await botCollaborationController.getEditorRequestPolicy('bot/1');

    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot%2F1/editor-request-policy',
      expect.objectContaining({ method: 'GET' }),
    );
  });

  test('patches auto_approve without replacing the policy resource', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);

    await botCollaborationController.updateEditorRequestPolicy('bot/1', true);

    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot%2F1/editor-request-policy',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ auto_approve: true }) }),
    );
  });
});
