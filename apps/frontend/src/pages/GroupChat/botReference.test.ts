import { botReference } from './botReference';

const bot = { bot_id: 'bot', owner_id: 'new', entity_id: 'stable' };

const cases: Array<[Parameters<typeof botReference>[0], string]> = [
  [{ ...bot, bot_type: 'service' }, 'bot:stable'],
  [{ ...bot, bot_type: 'service', ext: { bcn_provider_bot_ref: 'bot:original' } }, 'bot:original'],
  [{ ...bot, bot_type: 'personal' }, 'bot:new'],
  [{ ...bot, bot_type: 'desktop' }, 'bot:new'],
  [{ ...bot, bot_type: 'service', bot_id: 'default' }, 'default:new'],
];

test.each(cases)('retains registered BCN address for %o', (value, expected) => {
  expect(botReference(value)).toBe(expected);
});
