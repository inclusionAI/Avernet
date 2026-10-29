// Bot 公开风险前置检测 Controller 单测：四端点路径/方法/query 拼装、
// user_id 必填注入、202000/200000 双码语义、data:null 容忍、refresh 的 {outcome, record}。

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));

import {
  createBotPublicRiskCheck,
  getBotPublicRiskCheck,
  getCurrentBotPublicRiskCheck,
  refreshBotPublicRiskCheck,
} from '@/services/backendApi/collaboration/collaborationBotPublicRiskController';
import { backendRequest } from '@/services/backendApi/httpClient';

const request = jest.mocked(backendRequest);

beforeEach(() => {
  request.mockReset();
});

describe('collaborationBotPublicRiskController', () => {
  it('creates a risk check with user_id query and full target body', async () => {
    request.mockResolvedValueOnce({
      code: 202000,
      message: 'Accepted',
      data: { risk_check_id: 'rk-1', phase: 'PREPARING' },
    });
    const res = await createBotPublicRiskCheck('bot-1:entity-1', 'u1', {
      client_request_id: 'req-1',
      public_scope: 'user',
      visibility: 'public',
      view_depts: [],
    });
    expect(res.data?.risk_check_id).toBe('rk-1');
    const [url, options] = request.mock.calls[0] as unknown as [string, Record<string, unknown>];
    expect(url).toBe('/openapi/v1/collaboration/bots/bot-1:entity-1/public/risk-checks');
    expect(options).toMatchObject({
      method: 'POST',
      params: { user_id: 'u1' },
      injectUserId: false,
    });
    expect(options.data).toEqual({
      client_request_id: 'req-1',
      public_scope: 'user',
      visibility: 'public',
      view_depts: [],
    });
  });

  it('supports idempotent-replay-shaped envelopes (200000) transparently', async () => {
    request.mockResolvedValueOnce({ code: 200000, message: 'OK', data: { risk_check_id: 'rk-1', phase: 'CHECKING' } });
    const res = await createBotPublicRiskCheck('b', 'u', {
      client_request_id: 'req-same',
      public_scope: 'user',
      visibility: 'protected',
      view_depts: [{ deptNo: 'D1', deptName: 'A部' }],
    });
    expect(res.data?.phase).toBe('CHECKING');
  });

  it('queries current with required user_id and public_scope', async () => {
    request.mockResolvedValueOnce({ code: 200000, message: 'OK', data: null });
    const res = await getCurrentBotPublicRiskCheck('b:port', 'u1', { public_scope: 'user' });
    expect(res.data).toBeNull();
    const [url, options] = request.mock.calls[0] as unknown as [string, Record<string, unknown>];
    expect(url).toBe('/openapi/v1/collaboration/bots/b:port/public/risk-checks/current');
    expect(options.params).toEqual({ user_id: 'u1', public_scope: 'user' });
    expect(options.method).toBe('GET');
  });

  it('fetches a record by risk_check_id', async () => {
    request.mockResolvedValueOnce({ code: 200000, message: 'OK', data: { risk_check_id: 'rk-9' } });
    await getBotPublicRiskCheck('b', 'u1', 'rk-9');
    const [url, options] = request.mock.calls[0] as unknown as [string, Record<string, unknown>];
    expect(url).toBe('/openapi/v1/collaboration/bots/b/public/risk-checks/rk-9');
    expect(options.method).toBe('GET');
    expect(options.params).toEqual({ user_id: 'u1' });
  });

  it('refreshes without a body and returns {outcome, record}', async () => {
    request.mockResolvedValueOnce({
      code: 200000,
      message: 'OK',
      data: { outcome: 'NOT_QUERIED', record: { risk_check_id: 'rk-9', phase: 'CHECKING' } },
    });
    const res = await refreshBotPublicRiskCheck('b', 'u1', 'rk-9');
    expect(res.data?.outcome).toBe('NOT_QUERIED');
    const [url, options] = request.mock.calls[0] as unknown as [string, Record<string, unknown>];
    expect(url).toBe('/openapi/v1/collaboration/bots/b/public/risk-checks/rk-9/refresh');
    expect(options.method).toBe('POST');
    expect(options.data).toBeUndefined();
  });
});
