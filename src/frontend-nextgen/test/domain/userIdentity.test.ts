import { isSameHumanIdentity, normalizeHumanUserId, resolveAuthenticatedDisplayName } from '@/domain/userIdentity';
import { describe, expect, it } from '@jest/globals';

describe('user identity display matching', () => {
  it.each([
    ['human_900004', '900004'],
    ['user_id:900004', '900004'],
    ['900004', '900004'],
  ])('normalizes known human id %s to %s', (candidate, expected) => {
    expect(normalizeHumanUserId(candidate, 'human')).toBe(expected);
  });

  it('does not treat the me placeholder as a real user id', () => {
    expect(normalizeHumanUserId('me', 'human')).toBe('');
    expect(isSameHumanIdentity('me', '900004', 'human')).toBe(false);
  });

  it('does not normalize Bot compound ids into human ids', () => {
    expect(normalizeHumanUserId('bot_xxx:900004')).toBe('');
    expect(normalizeHumanUserId('bot_xxx:900004', 'bot')).toBe('');
    expect(isSameHumanIdentity('bot_xxx:900004', '900004')).toBe(false);
  });

  it('matches the authenticated human only by canonical id', () => {
    expect(isSameHumanIdentity('human_900004', '900004', 'human')).toBe(true);
    expect(isSameHumanIdentity('human_447148', '900004', 'human')).toBe(false);
  });

  it('uses the authenticated name only for the matching human', () => {
    expect(
      resolveAuthenticatedDisplayName(
        { id: 'human_900004', kind: 'human', name: '旧名称' },
        { userId: '900004', name: '示例用户' },
      ),
    ).toBe('示例用户');
    expect(
      resolveAuthenticatedDisplayName(
        { id: 'human_447148', kind: 'human', name: '其他成员' },
        { userId: '900004', name: '示例用户' },
      ),
    ).toBe('其他成员');
  });

  it('does not borrow the authenticated name when another human has no name', () => {
    expect(
      resolveAuthenticatedDisplayName(
        { id: 'human_447148', kind: 'human', name: '' },
        { userId: '900004', name: '示例用户' },
      ),
    ).toBe('human_447148');
  });

  it('falls back to the candidate name and id when the authenticated name is empty', () => {
    expect(
      resolveAuthenticatedDisplayName(
        { id: 'human_900004', kind: 'human', name: '会话回显名称' },
        { userId: '900004', name: '  ' },
      ),
    ).toBe('会话回显名称');
    expect(resolveAuthenticatedDisplayName({ id: 'human_900004', kind: 'human' }, { userId: '900004', name: '' })).toBe(
      'human_900004',
    );
  });
});
