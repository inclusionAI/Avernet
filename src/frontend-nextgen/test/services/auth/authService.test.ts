import { selectAlipayLoginUrl, toAuthUser } from '@/services/auth/authService';

describe('authService', () => {
  it('maps the existing BCS /auth/user response', () => {
    expect(toAuthUser({ user_id: 'u-1', name: 'Alice', provider: 'alipay', avatar: null })).toEqual({
      userId: 'u-1',
      displayName: 'Alice',
      provider: 'alipay',
      avatarUrl: undefined,
    });
  });

  it('selects only the configured Alipay provider URL', () => {
    expect(selectAlipayLoginUrl({ providers: [{ name: 'alipay', url: 'https://example.test/login' }] })).toBe(
      'https://example.test/login',
    );
    expect(selectAlipayLoginUrl({ providers: [] })).toBeNull();
  });
});
