/**
 * The identity Bot is minted per deployment; nothing may hardcode it. These
 * cases pin that the picker is driven entirely by the runtime Bot listing.
 */
import { toIdentityOptions } from '@/domain/agentswarm/identityOptions';
import type { ChatBotView } from '@/services/workspace/botSessionService';

const bot = (partial: Partial<ChatBotView>): ChatBotView =>
  ({
    botId: 'b',
    realBotId: 'b',
    displayName: 'Bot',
    online: true,
    chatable: true,
    status: 'ACTIVE',
    ...partial,
  }) as ChatBotView;

test('a freshly minted id from the listing becomes the option value', () => {
  // Arbitrary, deployment-specific: the picker must not care what it looks like.
  const [option] = toIdentityOptions([bot({ realBotId: '1719999999_zz9zz9zz', displayName: '新部署 Bot' })]);
  expect(option.botId).toBe('1719999999_zz9zz9zz');
});

test('the option set is exactly the listing — nothing is added or assumed', () => {
  const listed = [
    bot({ realBotId: 'a1', displayName: 'A' }),
    bot({ realBotId: 'b2', displayName: 'B' }),
  ];
  expect(toIdentityOptions(listed).map((option) => option.botId)).toEqual(['a1', 'b2']);
});

test('a redeploy that renames every id yields the renamed ids, not a cached one', () => {
  const before = toIdentityOptions([bot({ realBotId: 'old_deploy_bot' })]);
  const after = toIdentityOptions([bot({ realBotId: 'new_deploy_bot' })]);
  expect(before[0].botId).toBe('old_deploy_bot');
  expect(after[0].botId).toBe('new_deploy_bot');
  expect(after.map((option) => option.botId)).not.toContain('old_deploy_bot');
});

test('a compound bot address is split to the real id the gateway expects', () => {
  // The listing carries `botId` as head:tail; `identity_bot_id` takes the head.
  const [option] = toIdentityOptions([bot({ botId: 'head0123:49912', realBotId: 'head0123' })]);
  expect(option.botId).toBe('head0123');
});

test('an empty listing yields no options, so the form shows its empty state', () => {
  expect(toIdentityOptions([])).toEqual([]);
});