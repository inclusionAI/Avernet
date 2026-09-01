import { backendRequest } from '@/services/backendApi/httpClient';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

// httpClient 经 useIdentityStore.getState() 注入 user_id,按既有 httpClient.test.ts 范式 mock 为固定身份。
jest.mock('@/stores/identityStore', () => ({
  useIdentityStore: {
    getState: () => ({ currentIdentityId: 'human-1' }),
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

function jsonStatus(status: number, body: unknown): Response {
  return {
    ok: false,
    status,
    headers: { get: () => 'application/json' },
    json: async () => body,
  } as unknown as Response;
}

const aceBody = (buserviceErrorMsg = 'https://login.example.com/pubLogin?goto=x') => ({
  actionType: 'LOGIN',
  buserviceErrorCode: 'USER_NOT_LOGIN',
  decisionBy: 'ACE',
  buserviceErrorMsg,
});

beforeEach(() => {
  useLoginRedirectStore.getState().reset();
  useLoginStrategyStore.getState().setLoginStrategy('ace-gateway'); // 默认内部（既有行为），逐用例覆写
});

afterEach(() => {
  global.fetch = originalFetch;
});

describe('httpClient ACE 登录拦截探测', () => {
  it('HTTP 200 + ACE body → 登记单飞跳转并抛 AceLoginRedirectError', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonOk(aceBody('https://login/first')));

    await expect(backendRequest('/openapi/v1/admin/spaces', { injectUserId: false })).rejects.toMatchObject({
      name: 'AceLoginRedirectError',
      loginUrl: 'https://login/first',
    });

    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe('https://login/first');
  });

  it('正常 200 业务体不触发跳转、正常返回数据', async () => {
    global.fetch = jest
      .fn<typeof fetch>()
      .mockResolvedValue(jsonOk({ code: 200000, message: '', data: { items: [], total: 0 }, request_id: 'r' }));

    const data = await backendRequest('/openapi/v1/admin/spaces', { injectUserId: false });

    expect(data).toMatchObject({ code: 200000 });
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBeUndefined();
  });

  it('HTTP 401 不触发 ACE 跳转(走既有 BackendRequestError 路径)', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonStatus(401, { message: 'unauthorized' }));

    await expect(backendRequest('/openapi/v1/admin/spaces', { injectUserId: false })).rejects.toMatchObject({
      name: 'BackendRequestError',
      status: 401,
    });

    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBeUndefined();
  });

  it('单飞:并发两次 ACE 命中只登记首个 URL', async () => {
    const fetchMock = jest
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonOk(aceBody('https://login/first')))
      .mockResolvedValueOnce(jsonOk(aceBody('https://login/second')));
    global.fetch = fetchMock;

    await expect(backendRequest('/openapi/v1/admin/spaces', { injectUserId: false })).rejects.toMatchObject({
      name: 'AceLoginRedirectError',
      loginUrl: 'https://login/first',
    });
    await expect(backendRequest('/openapi/v1/admin/spaces', { injectUserId: false })).rejects.toMatchObject({
      name: 'AceLoginRedirectError',
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe('https://login/first');
  });

  it('ACE 体缺失 buserviceErrorMsg 仍抛错但不登记跳转', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(
      jsonOk({
        actionType: 'LOGIN',
        buserviceErrorCode: 'USER_NOT_LOGIN',
        decisionBy: 'ACE',
      }),
    );

    await expect(backendRequest('/openapi/v1/admin/spaces', { injectUserId: false })).rejects.toMatchObject({
      name: 'AceLoginRedirectError',
    });

    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBeUndefined();
  });

  it('oauth-provider 策略下 ACE 体 → 弹窗信号(不硬跳转),仍抛 AceLoginRedirectError', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonOk(aceBody('https://login/ace')));

    await expect(backendRequest('/openapi/v1/admin/spaces', { injectUserId: false })).rejects.toMatchObject({
      name: 'AceLoginRedirectError',
    });

    // prompt 模式：不登记硬跳转 url，只置 prompt 信号。
    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBeUndefined();
  });
});
