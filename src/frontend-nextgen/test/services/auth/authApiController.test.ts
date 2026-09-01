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
    ['providers', () => getAuthProviders(), AUTH_ENDPOINTS.providers, 'GET'],
    ['current user', () => getCurrentAuthUser(), AUTH_ENDPOINTS.currentUser, 'GET'],
    ['refresh', () => refreshAuthSession(), AUTH_ENDPOINTS.refresh, 'POST'],
    ['logout', () => logoutAuthSession(), AUTH_ENDPOINTS.logout, 'POST'],
  ])('calls the BCS compatibility endpoint for %s with cookies', async (_name, invoke, endpoint, method) => {
    (request as jest.Mock).mockResolvedValue({});
    await invoke();
    expect(request).toHaveBeenCalledWith(endpoint, { method, credentials: 'include', skipErrorHandler: true });
  });

  it('/auth/user 401 经 skipErrorHandler 原样透传(axios 形错误,不吞错)', async () => {
    const axiosErr = { response: { status: 401, data: { message: 'unauthorized' } } };
    (request as jest.Mock).mockRejectedValue(axiosErr);
    await expect(getCurrentAuthUser()).rejects.toBe(axiosErr);
  });

  it('/auth/url 正常 provider 体原样返回(不经业务失败标准化)', async () => {
    const providers = { providers: [{ name: 'alipay', url: 'https://example.test/login' }] };
    (request as jest.Mock).mockResolvedValue(providers);
    await expect(getAuthProviders()).resolves.toEqual(providers);
  });
});
