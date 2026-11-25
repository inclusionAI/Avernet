import { findIdentityOption, toIdentityOptions } from '@/domain/agentswarm/identityOptions';
import type { ChatBotView } from '@/services/workspace/botSessionService';

const bot = (partial: Partial<ChatBotView>): ChatBotView =>
  ({ botId: 'bot-1', realBotId: 'bot-1', displayName: '数据分析助手', online: true, chatable: true, ...partial }) as ChatBotView;

test('an active owned bot becomes a selectable option keyed by its real id', () => {
  const [option] = toIdentityOptions([bot({ botId: 'head:tail', realBotId: 'head', status: 'ACTIVE' })]);
  expect(option).toMatchObject({ botId: 'head', displayName: '数据分析助手', selectable: true });
  expect(option.disabledReason).toBeUndefined();
});

test('a missing status is treated as usable rather than blocking the picker', () => {
  expect(toIdentityOptions([bot({ status: undefined })])[0].selectable).toBe(true);
});

test('a bot in a non-active state is listed but not selectable, with the reason', () => {
  const [option] = toIdentityOptions([bot({ status: 'FAILED' })]);
  expect(option.selectable).toBe(false);
  expect(option.disabledReason).toContain('FAILED');
});

test('a bot with no identifier is not selectable', () => {
  const [option] = toIdentityOptions([bot({ botId: '', realBotId: '' })]);
  expect(option.selectable).toBe(false);
  expect(option.botId).toBe('');
});

test('an empty bot list yields no options so the form can render its empty state', () => {
  expect(toIdentityOptions([])).toEqual([]);
});

test('finding a selection by id, including when nothing is selected', () => {
  const options = toIdentityOptions([bot({ realBotId: 'b1' }), bot({ realBotId: 'b2' })]);
  expect(findIdentityOption(options, 'b2')?.botId).toBe('b2');
  expect(findIdentityOption(options, null)).toBeUndefined();
  expect(findIdentityOption(options, '')).toBeUndefined();
  expect(findIdentityOption(options, 'gone')).toBeUndefined();
});

test('selectable does not claim passport issuance, which the bot list cannot report', () => {
  const [option] = toIdentityOptions([bot({ status: 'ACTIVE' })]);
  // Usable is not proven authorized: no field claims an issued passport,
  // and the gateway still verifies the source at submit.
  expect(Object.keys(option).sort()).toEqual(
    ['avatarUrl', 'botId', 'disabledReason', 'displayName', 'online', 'selectable'].sort(),
  );
  expect(option.selectable).toBe(true);
});