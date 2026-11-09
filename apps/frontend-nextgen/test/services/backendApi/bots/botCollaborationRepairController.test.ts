import { repairBotCollaboration } from '@/services/backendApi/bots/botCollaborationRepairController';
import * as httpClient from '@/services/backendApi/httpClient';
import { beforeEach, describe, expect, jest, test } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient');
const backendRequest = httpClient.backendRequest as jest.MockedFunction<typeof httpClient.backendRequest>;

beforeEach(() => {
  jest.resetAllMocks();
});

describe('botCollaborationRepairController', () => {
  test('repair is one empty-body POST', async () => {
    backendRequest.mockResolvedValue({ code: 200000, data: null });

    await repairBotCollaboration('bot:a/b', 'u1');

    const [url, options] = backendRequest.mock.calls[0];
    expect(url).toBe('/openapi/v1/bots/bot%3Aa%2Fb/collaboration/repair');
    expect(options).toMatchObject({
      method: 'POST',
      params: { user_id: 'u1' },
      retryOnTransient: false,
      operation: 'repair-bot-collaboration',
    });
    expect(options).not.toHaveProperty('data');
    expect(options).not.toHaveProperty('rawBody');
    expect(backendRequest).toHaveBeenCalledTimes(1);
  });
});
