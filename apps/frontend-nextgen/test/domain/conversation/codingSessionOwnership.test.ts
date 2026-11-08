import {
  codingSessionOriginOf,
  extractCodingSessionCreatorUserId,
  formatCodingOtherSessionTitle,
  isSameCodingSessionUserId,
  resolveCodingSessionOrigin,
} from '@/domain/conversation/codingSessionOwnership';

describe('codingSessionOwnership', () => {
  it.each([
    ['user:382716:session:uuid:agent:bot', '382716'],
    ['agent:main:session:uuid:user:100000', '100000'],
    ['agent:main:dingtalk:direct:100001', '100001'],
  ])('extracts creator from %s', (sessionId, expected) => {
    expect(extractCodingSessionCreatorUserId(sessionId)).toBe(expected);
  });

  it('prefixes another Coding session title with its creator user id', () => {
    expect(formatCodingOtherSessionTitle('user:400001:session:shared:agent:coding-bot', '排查构建失败')).toBe(
      '400001: 排查构建失败',
    );
    expect(formatCodingOtherSessionTitle('legacy-session-id', '历史会话')).toBe('历史会话');
  });

  it('compares numeric staff ids without leading zero differences', () => {
    expect(isSameCodingSessionUserId('011111', '11111')).toBe(true);
    expect(codingSessionOriginOf('user:011111:session:uuid:agent:bot', '11111')).toBe('mine');
  });

  it('classifies another creator as others', () => {
    expect(codingSessionOriginOf('user:382716:session:uuid:agent:bot', '382717')).toBe('others');
    expect(resolveCodingSessionOrigin('agent:main:session:uuid:user:382716', '382717')).toBe('others');
  });

  it('keeps legacy unrecognized ids in mine but does not force a deep-link source', () => {
    expect(codingSessionOriginOf('legacy-session-id', '382716')).toBe('mine');
    expect(resolveCodingSessionOrigin('legacy-session-id', '382716')).toBeNull();
  });
});
