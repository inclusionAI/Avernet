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
});
