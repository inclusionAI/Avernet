import { backendRequest } from '@/services/backendApi/httpClient';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
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

// 未登录反应口与静默(external-oauth-login「未登录静默与统一登录处置」):
// oauth 策略下未登录失败(HTTP 401 任意体 / 信封双方言 401 段体,含网关误包 HTTP 200 形态)→ 单飞登记登录弹窗信号,
// 静默上抛 AceLoginRedirectError,不投递逐条通用错误 toast;已确认未登录态后的其余失败同样静默。
// ace-gateway 行为不变(spec「登录处置信号统一出口与单飞」外部模式弹窗 Scenario)。
describe('httpClient 未登录反应口与静默', () => {
  const bcsUnauthBody = {
    code: 40100,
    message: 'Authentication is required',
    data: { error_code: 'unauthenticated' },
    request_id: 'r',
  };

  beforeEach(() => {
    useLoginRedirectStore.getState().reset();
    useErrorNotifyStore.setState({ queue: [] });
    useExternalAuthStore.setState({ status: 'unknown', user: null, error: null, isCheckingAuth: false });
  });

  it('oauth-provider + 401 + unauthenticated 体 → prompt 信号 + 抛 AceLoginRedirectError,不入错误提示队列', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonStatus(401, bcsUnauthBody));

    await expect(backendRequest('/openapi/v1/collaboration/sessions', { injectUserId: false })).rejects.toMatchObject({
      name: 'AceLoginRedirectError',
    });

    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });

  it('oauth-provider + 401 + 其他错误体(HTTP 401 即未登录语义)→ prompt 信号 + 静默抛 AceLoginRedirectError', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    global.fetch = jest
      .fn<typeof fetch>()
      .mockResolvedValue(jsonStatus(401, { code: 40300, data: { error_code: 'forbidden' } }));

    await expect(backendRequest('/openapi/v1/collaboration/sessions', { injectUserId: false })).rejects.toMatchObject({
      name: 'AceLoginRedirectError',
    });

    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });

  it('oauth-provider + 401 + 无信封裸体 → prompt 信号 + 静默抛 AceLoginRedirectError', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonStatus(401, { message: 'unauthorized' }));

    await expect(backendRequest('/openapi/v1/collaboration/sessions', { injectUserId: false })).rejects.toMatchObject({
      name: 'AceLoginRedirectError',
    });

    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });

  it('oauth-provider + HTTP 200 + 网关误包 40100(BCS 5 位,无 error_code)→ prompt 信号 + 静默抛 AceLoginRedirectError', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    global.fetch = jest
      .fn<typeof fetch>()
      .mockResolvedValue(jsonOk({ code: 40100, message: 'Authentication is required', request_id: 'r' }));

    await expect(backendRequest('/openapi/v1/collaboration/sessions', { injectUserId: false })).rejects.toMatchObject({
      name: 'AceLoginRedirectError',
    });

    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });

  it('oauth-provider + HTTP 200 + 网关误包 401000(python 6 位)→ prompt 信号 + 静默抛 AceLoginRedirectError', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    global.fetch = jest
      .fn<typeof fetch>()
      .mockResolvedValue(jsonOk({ code: 401000, message: '未登录', request_id: 'r' }));

    await expect(backendRequest('/openapi/v1/collaboration/sessions', { injectUserId: false })).rejects.toMatchObject({
      name: 'AceLoginRedirectError',
    });

    expect(useLoginRedirectStore.getState().pendingLogin).toEqual({ mode: 'prompt' });
    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });

  it('oauth-provider + 已确认未登录 + 非 401 失败(500)→ 静默抛 BackendRequestError,不入错误提示队列', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('oauth-provider');
    useExternalAuthStore.getState().setUnauthenticated();
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonStatus(500, { code: 500001, message: '服务异常' }));

    await expect(backendRequest('/openapi/v1/collaboration/sessions', { injectUserId: false })).rejects.toMatchObject({
      name: 'BackendRequestError',
      status: 500,
    });

    expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  });

  it('ace-gateway + 401 + unauthenticated 体 → 行为不变(BackendRequestError,无弹窗信号)', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonStatus(401, bcsUnauthBody));

    await expect(backendRequest('/openapi/v1/admin/spaces', { injectUserId: false })).rejects.toMatchObject({
      name: 'BackendRequestError',
      status: 401,
    });

    expect(useLoginRedirectStore.getState().pendingLogin).toBeUndefined();
  });

  it('ace-gateway + 已确认未登录态 + 500 → 行为不变(照常投递默认提示)', async () => {
    useLoginStrategyStore.getState().setLoginStrategy('ace-gateway');
    useExternalAuthStore.getState().setUnauthenticated();
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonStatus(500, { code: 500001, message: '服务异常' }));

    await expect(backendRequest('/openapi/v1/admin/spaces', { injectUserId: false })).rejects.toMatchObject({
      name: 'BackendRequestError',
      status: 500,
    });

    expect(useErrorNotifyStore.getState().queue).toHaveLength(1);
  });
});
