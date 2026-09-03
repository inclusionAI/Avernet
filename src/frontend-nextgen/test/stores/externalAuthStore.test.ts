import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { beforeEach, describe, expect, it } from '@jest/globals';

describe('externalAuthStore', () => {
  beforeEach(() => useExternalAuthStore.getState().reset());

  it('初始 status=unknown、user/loginUrl 空、无 loading', () => {
    const s = useExternalAuthStore.getState();
    expect(s.status).toBe('unknown');
    expect(s.user).toBeNull();
    expect(s.loginUrl).toBeNull();
    expect(s.isCheckingAuth).toBe(false);
    expect(s.isLoadingLoginUrl).toBe(false);
    expect(s.isLoggingOut).toBe(false);
    expect(s.error).toBeNull();
  });

  it('setAuthenticated 置 authenticated + user + 清 error/isCheckingAuth', () => {
    useExternalAuthStore
      .getState()
      .setAuthenticated({ userId: 'u-1', displayName: 'Alice', provider: 'alipay', avatarUrl: 'https://a' });
    const s = useExternalAuthStore.getState();
    expect(s.status).toBe('authenticated');
    expect(s.user).toEqual({ userId: 'u-1', displayName: 'Alice', provider: 'alipay', avatarUrl: 'https://a' });
    expect(s.error).toBeNull();
    expect(s.isCheckingAuth).toBe(false);
  });

  it('setUnauthenticated 置 unauthenticated + 清 user', () => {
    useExternalAuthStore.getState().setAuthenticated({ userId: 'u', displayName: 'A', provider: 'alipay' });
    useExternalAuthStore.getState().setUnauthenticated();
    const s = useExternalAuthStore.getState();
    expect(s.status).toBe('unauthenticated');
    expect(s.user).toBeNull();
    expect(s.isCheckingAuth).toBe(false);
  });

  it('setAuthError 置 error + 清 user', () => {
    useExternalAuthStore.getState().setAuthError('boom');
    const s = useExternalAuthStore.getState();
    expect(s.status).toBe('error');
    expect(s.error).toBe('boom');
    expect(s.user).toBeNull();
  });

  it('setLoginUrl / loadings 标志位', () => {
    useExternalAuthStore.getState().setLoginUrl('https://login.example/a');
    expect(useExternalAuthStore.getState().loginUrl).toBe('https://login.example/a');
    useExternalAuthStore.getState().setCheckingAuth(true);
    expect(useExternalAuthStore.getState().isCheckingAuth).toBe(true);
    useExternalAuthStore.getState().setLoadingLoginUrl(true);
    expect(useExternalAuthStore.getState().isLoadingLoginUrl).toBe(true);
    useExternalAuthStore.getState().setLoggingOut(true);
    expect(useExternalAuthStore.getState().isLoggingOut).toBe(true);
  });
});
