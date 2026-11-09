import { resolveBotSenderPresentation } from '@/pages/Workspace/components/GroupChatPane/botSenderPresentation';
import type { ParticipantSummaryView } from '@/domain/collaboration';
import { expect, test } from '@jest/globals';

const bot: ParticipantSummaryView = { actorId: 'b1', kind: 'bot', name: 'Member name', avatarUrl: '/member.png' };
test.each(['senderName', 'botName'])('%s does not bypass member avatar', (field) => {
  expect(resolveBotSenderPresentation({ status: 'done', extra: { senderId: 'b1', [field]: 'Wire name' } }, [bot], []))
    .toEqual({ botId: 'b1', name: 'Wire name', avatarUrl: '/member.png' });
});
test('session explicit absence does not reuse group or message old URL', () => {
  expect(resolveBotSenderPresentation({ status: 'done', extra: { senderId: 'b1', senderAvatarUrl: '/old.png' } },
    [{ ...bot, avatarUrl: undefined }], [bot])?.avatarUrl).toBeUndefined();
});
test('botUuid is canonical when senderId conflicts', () => {
  expect(resolveBotSenderPresentation({ status: 'done', extra: { botUuid: 'missing', senderId: 'b1' } }, [bot], []))
    .toMatchObject({ botId: 'missing', avatarUrl: undefined });
});
test('anonymous pending placeholder stays anonymous', () => {
  expect(resolveBotSenderPresentation({ status: 'pending', extra: {} }, [bot], [])).toBeUndefined();
});

test('same display name never selects by name', () => {
  const first: ParticipantSummaryView = { actorId: 'b1', kind: 'bot', name: '同名', avatarUrl: '/one.png' };
  const second: ParticipantSummaryView = { actorId: 'b2', kind: 'bot', name: '同名', avatarUrl: '/two.png' };
  expect(resolveBotSenderPresentation({ status: 'done', extra: { senderId: 'b2' } }, [first, second], []))
    .toMatchObject({ botId: 'b2', avatarUrl: '/two.png' });
});

test('session summary wins over group summary for the same bot id', () => {
  const groupBot: ParticipantSummaryView = { ...bot, avatarUrl: '/group.png' };
  const sessionBot: ParticipantSummaryView = { ...bot, avatarUrl: '/session.png' };
  expect(resolveBotSenderPresentation({ status: 'done', extra: { senderId: 'b1' } }, [sessionBot], [groupBot]))
    .toMatchObject({ botId: 'b1', avatarUrl: '/session.png' });
});

test('group-only member supplies the avatar', () => {
  expect(resolveBotSenderPresentation({ status: 'done', extra: { senderId: 'b1' } }, [], [bot]))
    .toMatchObject({ botId: 'b1', avatarUrl: '/member.png' });
});

test('unmatched id falls back to the message avatar', () => {
  expect(resolveBotSenderPresentation({ status: 'history', extra: { senderId: 'gone', senderAvatarUrl: '/old.png' } }, [bot], []))
    .toEqual({ botId: 'gone', name: 'gone', avatarUrl: '/old.png' });
});

test('human member with the same actor id is never matched', () => {
  const human: ParticipantSummaryView = { actorId: 'b1', kind: 'human', name: 'Human', avatarUrl: '/human.png' };
  expect(resolveBotSenderPresentation({ status: 'done', extra: { senderId: 'b1' } }, [human], [human]))
    .toMatchObject({ botId: 'b1', avatarUrl: undefined });
});

test('no id uses the message avatar or the fixed bot seed', () => {
  expect(resolveBotSenderPresentation({ status: 'done', extra: { senderAvatarUrl: '/self.png' } }, [bot], []))
    .toEqual({ botId: 'bot', name: 'Bot', avatarUrl: '/self.png' });
  expect(resolveBotSenderPresentation({ status: 'done', extra: {} }, [bot], []))
    .toEqual({ botId: 'bot', name: 'Bot', avatarUrl: undefined });
});

test('a senderName equal to the raw id is not a readable name', () => {
  expect(resolveBotSenderPresentation(
    { status: 'done', extra: { botUuid: 'b1', senderId: 'b1', senderName: 'b1' } }, [bot], [],
  )).toMatchObject({ name: 'Member name' });
});

test('readable senderName keeps priority over botName and member name', () => {
  expect(resolveBotSenderPresentation(
    { status: 'done', extra: { senderId: 'b1', senderName: 'Wire', botName: 'WireBot' } }, [bot], [],
  )).toMatchObject({ name: 'Wire', avatarUrl: '/member.png' });
});
