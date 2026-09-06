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

  it('falls back to user_id when /auth/user name is empty', () => {
    expect(toAuthUser({ user_id: 'external-user-2', name: '   ', provider: 'github' })).toMatchObject({
      userId: 'external-user-2',
      displayName: 'external-user-2',
    });
  });

  it('selects only the configured Alipay provider URL', () => {
    expect(selectAlipayLoginUrl({ providers: [{ name: 'alipay', url: 'https://example.test/login' }] })).toBe(
      'https://example.test/login',
    );
    expect(selectAlipayLoginUrl({ providers: [] })).toBeNull();
  });
});
