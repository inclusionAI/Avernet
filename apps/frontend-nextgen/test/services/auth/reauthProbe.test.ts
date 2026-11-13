import { probeGatewaySession } from '@/services/auth/reauthProbe';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

/**
 * ace-gateway 探活判定(ace-gateway-popup-reauth D4,正证据制 —— 真机回归 #1 修正):
 * "非 ACE 即恢复"的负向判据在真机上被证伪(探活请求可能不经 ACE 拦截即返回 200 业务体/HTML),
 * 收口必须拿到肯定性已登录证据(2xx + JSON 对象 + 非未登录形态);不可判定一律维持等待。
 */

// 探活注入当前身份工号(org/user 为登录才可见的组织接口):
// capability 按既有 service 层用法是可 import 的注册表,mock 其身份解析即可。
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getCurrentOpenApiUserId: () => ({ status: 'available' as const, value: '12345' }),
  }),
}));
jest.mock('@/stores/workspaceStore', () => ({
  useWorkspaceStore: {
    getState: () => ({ activeIdentityId: 'identity-1' }),
  },
}));

const originalFetch = global.fetch;

function jsonOk(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => body,
  } as unknown as Response;
}

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  global.fetch = jest.fn();
});

afterEach(() => {
  global.fetch = originalFetch;
});

describe('probeGatewaySession 正证据制', () => {
  it('同源 GET 组织接口、credentials 含会话 cookie、注入当前身份工号', async () => {
    global.fetch = jest.fn().mockResolvedValue(jsonOk({ code: 200000, data: {} }));

    await probeGatewaySession();

    expect(global.fetch).toHaveBeenCalledWith('/openapi/v1/org/user?user_id=12345', {
      method: 'GET',
      credentials: 'include',
    });
  });

  it('ACE 登录拦截体 → 未恢复,透出网关本次下发的新鲜登录链接', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      jsonOk({
        actionType: 'LOGIN',
        buserviceErrorCode: 'USER_NOT_LOGIN',
        decisionBy: 'ACE',
        buserviceErrorMsg: 'https://login.example/pubLogin?goto=x',
      }),
    );

    await expect(probeGatewaySession()).resolves.toEqual({
      state: 'unauthenticated',
      loginUrl: 'https://login.example/pubLogin?goto=x',
    });
  });

  it('未登录业务信封(python 6 位 401000,HTTP 401)→ 未恢复,维持等待', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      jsonResponse(401, { code: 401000, message: '未登录', request_id: 'r' }),
    );

    await expect(probeGatewaySession()).resolves.toEqual({ state: 'unauthenticated' });
  });

  it('未登录业务信封(BCS 5 位 40100,网关误包 HTTP 200)→ 未恢复,维持等待', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValue(jsonOk({ code: 40100, message: 'Authentication is required', request_id: 'r' }));

    await expect(probeGatewaySession()).resolves.toEqual({ state: 'unauthenticated' });
  });

  it('2xx 业务信封(含业务错误码)→ 肯定性证据,判定已恢复', async () => {
    global.fetch = jest.fn().mockResolvedValue(jsonOk({ code: 400000, message: '参数错误', request_id: 'r' }));

    await expect(probeGatewaySession()).resolves.toEqual({ state: 'logged-in' });
  });

  it('2xx 业务信封(成功)→ 判定已恢复', async () => {
    global.fetch = jest.fn().mockResolvedValue(jsonOk({ code: 200000, message: 'OK', data: {}, request_id: 'r' }));

    await expect(probeGatewaySession()).resolves.toEqual({ state: 'logged-in' });
  });

  it('非 JSON 响应体(HTML 兜底页等)→ 不可判定,维持等待(不再误判已恢复)', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => 'text/html' },
      json: async () => {
        throw new Error('not json');
      },
    } as unknown as Response);

    await expect(probeGatewaySession()).resolves.toEqual({ state: 'unknown' });
  });

  it('非 2xx 且非未登录信封(JSON body)→ 不可判定,维持等待', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValue(jsonResponse(502, { code: 500001, message: 'Bad Gateway', request_id: 'r' }));

    await expect(probeGatewaySession()).resolves.toEqual({ state: 'unknown' });
  });

  it('2xx 但 JSON 解析为 null → 不可判定,维持等待', async () => {
    global.fetch = jest.fn().mockResolvedValue(jsonOk(null));

    await expect(probeGatewaySession()).resolves.toEqual({ state: 'unknown' });
  });

  it('网络失败 → 不可判定,维持等待(不误收口)', async () => {
    global.fetch = jest.fn().mockRejectedValue(new TypeError('Failed to fetch'));

    await expect(probeGatewaySession()).resolves.toEqual({ state: 'unknown' });
  });
});
