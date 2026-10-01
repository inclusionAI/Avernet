import { resolveBotFriendPlatform } from '@/domain/botFriendPlatform';
import { describe, expect, it } from '@jest/globals';

describe('resolveBotFriendPlatform', () => {
  it.each([
    ['20260528_udt1y38n:327325', 'teamclaw'],
    ['bot_partner', 'third_party'],
    ['bot_partner:327325', 'teamclaw'],
    ['TC-GRPCHAT-ProfileToggleBot-n5r8', null],
    ['bot_', null],
    ['missing-owner:', null],
    [':missing-bot', null],
    ['a:b:c', null],
    ['bot_a:b:c', null],
  ] as const)('classifies %s as %s', (botUuid, expected) => {
    expect(resolveBotFriendPlatform(botUuid)).toBe(expected);
  });
});
