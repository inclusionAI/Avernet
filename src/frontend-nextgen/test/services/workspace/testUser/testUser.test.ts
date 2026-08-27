import {
  ENABLE_TEST_USER,
  isTestUserIdentity,
  TEST_USER_IDENTITY,
  TEST_USER_IDENTITY_ID,
} from '@/services/workspace/testUser/index';
import { describe, expect, it, jest } from '@jest/globals';
// `testUser` re-exports `TEAMCLAW_SUPPORT_BOT` from `../supportProvider`,后者 transitive 加载
// `@tc-chat/adapters`(node_modules 内 ESM),jest 直接 load 会报 SyntaxError,因此 stub 掉。
jest.mock('@tc-chat/adapters', () => ({}));

describe('testUser module', () => {
  it('暴露合成身份常量与开关', () => {
    expect(TEST_USER_IDENTITY_ID).toBe('test-user');
    expect(ENABLE_TEST_USER).toBe(false);
    expect(TEST_USER_IDENTITY).toMatchObject({ id: 'test-user', kind: 'user', displayName: '测试用户' });
  });
  it('isTestUserIdentity 判别', () => {
    expect(isTestUserIdentity('test-user')).toBe(true);
    expect(isTestUserIdentity('me')).toBe(false);
    expect(isTestUserIdentity(null)).toBe(false);
  });
});
