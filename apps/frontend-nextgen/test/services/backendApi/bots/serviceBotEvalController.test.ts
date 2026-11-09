import { serviceBotEvalController } from '@/services/backendApi/bots/serviceBotEvalController';
import * as http from '@/services/backendApi/httpClient';

jest.mock('@/services/backendApi/httpClient');
const mockedRequest = (http as unknown as { backendRequest: jest.Mock }).backendRequest;

describe('serviceBotEvalController', () => {
  beforeEach(() => {
    mockedRequest.mockReset().mockResolvedValue({
      code: 201000,
      message: 'Created',
      data: {
        bot_id: 'bot-1',
        bot_version: '2',
        cert_state: { status: 'certified' },
      },
    });
  });

  test('201000 等 2xx 成功信封不会被误判为失败', async () => {
    const response = await serviceBotEvalController.detail('bot-1', '2', '990001996565');

    expect(response.success).toBe(true);
    expect(response.error_code).toBe(201000);
    expect(response.data).toMatchObject({ bot_id: 'bot-1', bot_version: '2' });
  });

  test('非 2xx 成功段信封判为失败', async () => {
    mockedRequest.mockResolvedValue({ code: 400000, message: 'Bad Request', data: null });

    const response = await serviceBotEvalController.detail('bot-1', '2', '990001996565');

    expect(response.success).toBe(false);
    expect(response.error_code).toBe(400000);
  });
});
