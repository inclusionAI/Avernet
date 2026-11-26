import { defaultIdentityBotId, type IdentityOption } from '@/domain/agentswarm/identityOptions';

const option = (partial: Partial<IdentityOption> & { botId: string }): IdentityOption => ({
  displayName: partial.botId,
  online: true,
  selectable: true,
  ...partial,
});

test('the first usable Bot is preselected', () => {
  expect(defaultIdentityBotId([option({ botId: 'b1' }), option({ botId: 'b2' })])).toBe('b1');
});

test('an online Bot wins over an offline one listed first', () => {
  // Passport status is invisible to this client, but liveness is not — prefer a
  // Bot that is demonstrably alive.
  expect(
    defaultIdentityBotId([option({ botId: 'off', online: false }), option({ botId: 'on', online: true })]),
  ).toBe('on');
});

test('unusable Bots are never preselected', () => {
  expect(
    defaultIdentityBotId([
      option({ botId: 'disabled', selectable: false, disabledReason: '状态异常' }),
      option({ botId: 'ok' }),
    ]),
  ).toBe('ok');
});

test('no usable Bot means no default, not an empty id', () => {
  expect(defaultIdentityBotId([])).toBeUndefined();
  expect(defaultIdentityBotId([option({ botId: 'x', selectable: false })])).toBeUndefined();
  expect(defaultIdentityBotId([option({ botId: '  ' })])).toBeUndefined();
});