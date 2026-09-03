jest.mock('@umijs/max', () => ({ request: jest.fn() }));

import {
  AUTH_ENDPOINTS,
  getAuthProviders,
  getCurrentAuthUser,
  logoutAuthSession,
  refreshAuthSession,
} from '@/services/auth/authApiController';
import { request } from '@umijs/max';

describe('authApiController', () => {
  beforeEach(() => jest.clearAllMocks());

  it.each([
    ['providers', () => getAuthProviders(), '/openapi/v1/auth/url', 'GET'],
    ['current user', () => getCurrentAuthUser(), '/openapi/v1/auth/user', 'GET'],
    ['refresh', () => refreshAuthSession(), '/openapi/v1/auth/refresh', 'POST'],
    ['logout', () => logoutAuthSession(), '/openapi/v1/auth/logout', 'POST'],
  ])('calls the BCS compatibility endpoint for %s with cookies', async (_name, invoke, endpoint, method) => {
    // 端点字面量硬编码锁定:BCS bcs-api-http 公开挂载 nest("/openapi/v1/auth")(网关 domains auth→bcs),
    // 非 BCS 内网直连的裸 /auth/*。改动挂载前缀时须同步此处(见 add-external-oauth-login 8.6)。
    (request as jest.Mock).mockResolvedValue({});
    await invoke();
    expect(request).toHaveBeenCalledWith(endpoint, { method, credentials: 'include', skipErrorHandler: true });
  });

  it('AUTH_ENDPOINTS 常量对齐 BCS 公网挂载前缀 /openapi/v1/auth/*', () => {
    expect(AUTH_ENDPOINTS).toEqual({
      providers: '/openapi/v1/auth/url',
      currentUser: '/openapi/v1/auth/user',
      refresh: '/openapi/v1/auth/refresh',
      logout: '/openapi/v1/auth/logout',
    });
  });

  it('/auth/user 401 经 skipErrorHandler 原样透传(axios 形错误,不吞错)', async () => {
    const axiosErr = { response: { status: 401, data: { message: 'unauthorized' } } };
    (request as jest.Mock).mockRejectedValue(axiosErr);
    await expect(getCurrentAuthUser()).rejects.toBe(axiosErr);
  });

  it('/auth/url 裸 provider 体(非信封形状)原样透传(本地 mock / 未包信封部署兼容)', async () => {
    const providers = { providers: [{ name: 'alipay', url: 'https://example.test/login' }] };
    (request as jest.Mock).mockResolvedValue(providers);
    await expect(getAuthProviders()).resolves.toEqual(providers);
  });

  it('/auth/user BCS 信封(20000)解包返回 data(身份四元组)', async () => {
    const user = { user_id: 'gYJ', name: '廖某', provider: 'alipay', avatar: 'https://a' };
    (request as jest.Mock).mockResolvedValue({ code: 20000, message: 'OK', data: user, request_id: 'r' });
    await expect(getCurrentAuthUser()).resolves.toEqual(user);
  });

  it('/auth/url BCS 信封(20000)解包返回 data(providers)', async () => {
    const providers = { providers: [{ name: 'alipay', url: 'https://example.test/login' }] };
    (request as jest.Mock).mockResolvedValue({ code: 20000, message: 'OK', data: providers, request_id: 'r' });
    await expect(getAuthProviders()).resolves.toEqual(providers);
  });

  it('信封形状但 code 非 2xx 段(HTTP 200 异常形态)按业务失败 reject', async () => {
    (request as jest.Mock).mockResolvedValue({
      code: 40100,
      message: 'Authentication is required',
      data: null,
      request_id: 'r',
    });
    await expect(getCurrentAuthUser()).rejects.toThrow('Authentication is required');
  });
});
