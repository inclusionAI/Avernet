import { standardEvaluationService } from '@/services/botWorkshop/standardEvaluationService';
import * as http from '@/services/backendApi/httpClient';

jest.mock('@/services/backendApi/bots/botController', () => ({
  userScopedParams: jest.fn(() => ({ user_id: '133525' })),
}));
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));

const backendRequest = (http as unknown as { backendRequest: jest.Mock }).backendRequest;

beforeEach(() => {
  backendRequest.mockReset();
});

describe('listEvaluationSets', () => {
  test('404000 Not Found 转成用户可读的 MASA 提示', async () => {
    backendRequest.mockResolvedValueOnce({
      code: 404000,
      message: 'Not Found',
      data: null,
      request_id: 'request-1',
    });

    await expect(
      standardEvaluationService.listEvaluationSets('20260828_r3fgibtt', '9', '133525'),
    ).rejects.toThrow('获取评测样本集失败：未找到对应的 MASA 评测智能体');
  });

  test('404 信封 message not_found 也转成用户可读的 MASA 提示', async () => {
    backendRequest.mockResolvedValueOnce({
      code: 404000,
      message: 'not_found',
      data: null,
      request_id: 'request-1',
    });

    await expect(
      standardEvaluationService.listEvaluationSets('20260828_r3fgibtt', '9', '133525'),
    ).rejects.toThrow('获取评测样本集失败：未找到对应的 MASA 评测智能体');
  });

  test('HTTP 404 Not Found 也转成用户可读的 MASA 提示', async () => {
    backendRequest.mockRejectedValueOnce({
      status: 404,
      message: 'Not Found',
      toastKey: 'sample-set-404',
    });

    await expect(
      standardEvaluationService.listEvaluationSets('20260828_r3fgibtt', '9', '133525'),
    ).rejects.toThrow('获取评测样本集失败：未找到对应的 MASA 评测智能体');
  });

  test('成功信封返回 data，不做错误映射', async () => {
    backendRequest.mockResolvedValueOnce({
      code: 200000,
      message: 'OK',
      data: { support: true, sets: [] },
      request_id: 'request-2',
    });

    await expect(
      standardEvaluationService.listEvaluationSets('20260828_r3fgibtt', '9', '133525'),
    ).resolves.toMatchObject({ support: true, sets: [] });
  });
});
