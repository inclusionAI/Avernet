import {
  CODEFUSE_TOKEN_ERROR_MESSAGES,
  validateCodefuseTokenOwner,
} from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/compat/token';

function toBase64(value: string) {
  if (typeof btoa === 'function') return btoa(value);
  return Buffer.from(value, 'utf-8').toString('base64');
}

function codefuseToken(userId: string) {
  return toBase64(JSON.stringify({ t: 'token-value', w: userId }));
}

describe('CodeFuse Token 是否属于当前登录人', () => {
  test('兼容 5 位纯数字工号的 0 前置差异', () => {
    expect(validateCodefuseTokenOwner(codefuseToken('011111'), '11111')).toBeNull();
    expect(validateCodefuseTokenOwner(codefuseToken('11111'), '011111')).toBeNull();
  });

  test('兼容归一化后少于 5 位的纯数字工号', () => {
    expect(validateCodefuseTokenOwner(codefuseToken('00123'), '123')).toBeNull();
    expect(validateCodefuseTokenOwner(codefuseToken('00001'), '1')).toBeNull();
  });

  test('0 前置差异不会掩盖不同工号', () => {
    expect(validateCodefuseTokenOwner(codefuseToken('011112'), '11111')).toBe(
      CODEFUSE_TOKEN_ERROR_MESSAGES.userMismatch,
    );
  });

  test('非纯数字身份仍按原字符串精确比较', () => {
    expect(validateCodefuseTokenOwner(codefuseToken('user-011111'), 'user-11111')).toBe(
      CODEFUSE_TOKEN_ERROR_MESSAGES.userMismatch,
    );
  });
});
