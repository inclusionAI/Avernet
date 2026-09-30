import type { TaskClaimGrantStrategy } from '@/capabilities';
import { grantTaskClaim, revokeTaskClaim } from '@/services/backendApi/collaboration/taskGrantController';
import { backendRequest } from '@/services/backendApi/httpClient';
import { isEnvelopeSuccess } from '@/services/backendApi/types';

jest.mock('@/capabilities', () => ({
  getCapabilities: jest.fn(),
}));
jest.mock('@/services/backendApi/httpClient', () => ({
  backendRequest: jest.fn(),
}));

// getCapabilities 由 jest.mock 占位为 jest.fn；按用例注入返回的 capability 集合。
import { getCapabilities } from '@/capabilities';
const cap = getCapabilities as jest.Mock;
const setStrategy = (strategy: TaskClaimGrantStrategy, apiBase = '/openapi/v1/collaboration/tasks') =>
  cap.mockReturnValue({
    getTaskClaimGrantStrategy: () => ({ status: 'available', value: strategy }),
    getTaskApiBase: () => ({ status: 'available', value: apiBase }),
  });

describe('taskGrantController 任务认领授权策略', () => {
  const be = backendRequest as jest.Mock;

  beforeEach(() => {
    be.mockReset();
  });

  test('skip 策略:grant 短路返回 granted 合成信封,不调后端', async () => {
    setStrategy('skip');
    const resp = await grantTaskClaim({ bcs_bot_id: 'bot:owner1' });
    expect(be).not.toHaveBeenCalled();
    expect(isEnvelopeSuccess(resp)).toBe(true);
    expect(resp.data?.grant_status).toBe('granted');
    expect(resp.data?.bcs_bot_id).toBe('bot:owner1');
    expect(resp.data?.api_key_prefix).toBe('');
  });

  test('skip 策略:revoke 短路返回 revoked 合成信封,不调后端', async () => {
    setStrategy('skip');
    const resp = await revokeTaskClaim({ bcs_bot_id: 'bot:owner1' });
    expect(be).not.toHaveBeenCalled();
    expect(isEnvelopeSuccess(resp)).toBe(true);
    expect(resp.data?.grant_status).toBe('revoked');
  });

  test('secbaas-relay 策略:grant 真调后端 /grant(透传 secbaas,不走短路)', async () => {
    setStrategy('secbaas-relay', '/api/v1/collaboration/tasks');
    be.mockResolvedValueOnce({ code: 200000, message: 'OK', data: {} });
    await grantTaskClaim({ bcs_bot_id: 'bot:owner1' });
    expect(be).toHaveBeenCalledTimes(1);
    const [url, opts] = be.mock.calls[0];
    expect(url).toBe('/api/v1/collaboration/tasks/grant');
    expect(opts.method).toBe('POST');
    expect(opts.data).toEqual({ bcs_bot_id: 'bot:owner1' });
    expect(opts.injectUserId).toBe(false);
  });

  test('secbaas-relay 策略:revoke 真调后端 /revoke', async () => {
    setStrategy('secbaas-relay', '/api/v1/collaboration/tasks');
    be.mockResolvedValueOnce({ code: 200000, message: 'OK', data: {} });
    await revokeTaskClaim({ bcs_bot_id: 'bot:owner1' });
    expect(be).toHaveBeenCalledTimes(1);
    expect(be.mock.calls[0][0]).toBe('/api/v1/collaboration/tasks/revoke');
  });
});
