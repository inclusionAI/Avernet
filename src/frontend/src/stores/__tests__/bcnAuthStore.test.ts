import { useBcnAuthStore } from '../bcnAuthStore';

beforeEach(() => {
  useBcnAuthStore.getState().reset();
});

describe('bcnAuthStore', () => {
  it('初始状态为 unknown 且无用户信息', () => {
    const state = useBcnAuthStore.getState();

    expect(state.status).toBe('unknown');
    expect(state.user).toBeNull();
    expect(state.loginUrl).toBeNull();
    expect(state.isCheckingAuth).toBe(false);
    expect(state.isLoadingLoginUrl).toBe(false);
    expect(state.isLoggingOut).toBe(false);
  });

  it('当认证成功时写入用户并结束检查态', () => {
    useBcnAuthStore.getState().setCheckingAuth(true);
    useBcnAuthStore.getState().setAuthenticated({
      userId: 'user-1',
      name: '测试用户',
      provider: 'github',
      avatar: null,
    });

    const state = useBcnAuthStore.getState();
    expect(state.status).toBe('authenticated');
    expect(state.user?.userId).toBe('user-1');
    expect(state.user?.provider).toBe('github');
    expect(state.isCheckingAuth).toBe(false);
    expect(state.error).toBeNull();
  });

  it('当未登录时清空用户并标记 unauthenticated', () => {
    useBcnAuthStore.getState().setAuthenticated({
      userId: 'user-1',
      name: '测试用户',
      provider: 'github',
      avatar: null,
    });

    useBcnAuthStore.getState().setUnauthenticated();

    const state = useBcnAuthStore.getState();
    expect(state.status).toBe('unauthenticated');
    expect(state.user).toBeNull();
    expect(state.isCheckingAuth).toBe(false);
  });

  it('登录地址和登出 loading 状态可独立更新', () => {
    useBcnAuthStore.getState().setLoadingLoginUrl(true);
    useBcnAuthStore.getState().setLoginUrl('https://example.com/login');
    useBcnAuthStore.getState().setLoggingOut(true);

    const state = useBcnAuthStore.getState();
    expect(state.isLoadingLoginUrl).toBe(true);
    expect(state.loginUrl).toBe('https://example.com/login');
    expect(state.isLoggingOut).toBe(true);
  });

  it('reset 后恢复初始状态', () => {
    useBcnAuthStore.getState().setAuthenticated({
      userId: 'user-1',
      name: '测试用户',
      provider: 'github',
      avatar: null,
    });
    useBcnAuthStore.getState().setLoginUrl('https://example.com/login');

    useBcnAuthStore.getState().reset();

    const state = useBcnAuthStore.getState();
    expect(state.status).toBe('unknown');
    expect(state.user).toBeNull();
    expect(state.loginUrl).toBeNull();
  });
});
