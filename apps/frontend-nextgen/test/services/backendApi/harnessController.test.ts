import {
  getHarnessDimHistory,
  getHarnessDimReport,
  startHarnessDiagnose,
} from '@/services/backendApi/harnessController';
import * as httpClient from '@/services/backendApi/httpClient';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient', () => {
  const { jest: jestGlobals } = require('@jest/globals');
  return { backendRequest: jestGlobals.fn() };
});
const backendRequest = (httpClient as unknown as { backendRequest: jest.Mock<(...args: any[]) => any> }).backendRequest;

beforeEach(() => {
  backendRequest.mockReset();
});

describe('harnessController', () => {
  it('starts diagnose through Avernet public harness endpoint', async () => {
    backendRequest.mockResolvedValue({
      code: 200000,
      data: { scan_id: 7, bot_id: 'b1', entity_id: 'u1', status: 'scanning' },
      message: 'OK',
      request_id: 'r',
    });

    await startHarnessDiagnose('b1', 'u1', { entity_type: 'staff', entity_id: 'u1', scan_type: 'full', layer: 'L1' });

    expect(backendRequest).toHaveBeenCalledWith('/openapi/v1/bots/b1/harness/diagnose', {
      method: 'POST',
      params: { user_id: 'u1' },
      data: { entity_type: 'staff', entity_id: 'u1', scan_type: 'full', layer: 'L1' },
    });
  });

  it('loads dim report and history from public harness endpoints', async () => {
    backendRequest
      .mockResolvedValueOnce({
        code: 200000,
        data: { bot_id: 'b1', entity_id: 'u1', items: [] },
        message: 'OK',
        request_id: 'r',
      })
      .mockResolvedValueOnce({
        code: 200000,
        data: { bot_id: 'b1', entity_id: 'u1', total: 0, page: 1, size: 20, items: [] },
        message: 'OK',
        request_id: 'r',
      });

    await getHarnessDimReport({ botId: 'b1', userId: 'u1', entityId: 'u1', botPublishId: 'p1' });
    await getHarnessDimHistory({ botId: 'b1', userId: 'u1', entityId: 'u1', page: 1, size: 20 });

    expect(backendRequest.mock.calls[0][0]).toBe('/openapi/v1/bots/b1/harness/dim-report');
    expect(backendRequest.mock.calls[0][1].params).toEqual({ user_id: 'u1', entity_id: 'u1', bot_publish_id: 'p1' });
    expect(backendRequest.mock.calls[1][0]).toBe('/openapi/v1/bots/b1/harness/dim-history');
    expect(backendRequest.mock.calls[1][1].params).toMatchObject({ user_id: 'u1', entity_id: 'u1', page: 1, size: 20 });
  });

  it('rejects empty envelope data', async () => {
    backendRequest.mockResolvedValue({ code: 200000, data: null, message: 'OK', request_id: 'r' });
    await expect(getHarnessDimReport({ botId: 'b1', userId: 'u1', entityId: 'u1' })).rejects.toThrow(
      '健康报告 返回为空',
    );
  });
});
