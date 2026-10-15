import { listBotContainers, restartBotContainer } from '@/services/backendApi/bots/botContainerController';
import * as httpClient from '@/services/backendApi/httpClient';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient');
const backendRequest = (httpClient as unknown as { backendRequest: jest.Mock<any> }).backendRequest;

beforeEach(() => {
  backendRequest.mockReset();
});

describe('botContainerController', () => {
  it('uses the Bot-scoped OpenAPI endpoint to load service instances', async () => {
    backendRequest.mockResolvedValue({ data: { bot_id: 'bot/a', summary: {}, instances: [] } } as never);

    await listBotContainers('bot/a');

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/bots/bot%2Fa/containers');
  });

  it('uses the Bot-scoped endpoint to restart one encoded instance', async () => {
    backendRequest.mockResolvedValue({ data: { accepted: true } } as never);

    await restartBotContainer('bot/a', 'instance/1');

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/bots/bot%2Fa/containers/instance%2F1/restart', {
      method: 'POST',
    });
  });
});
