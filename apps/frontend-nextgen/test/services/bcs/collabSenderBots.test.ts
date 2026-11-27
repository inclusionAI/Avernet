import type { ChatMessage } from '@tc-chat/core';
import {
  BCS_SYSTEM_MESSAGE_BOT_UUID,
  collectParticipantBots,
  collectSenderBots,
  senderBotKey,
} from '@/services/bcs/collabSenderBots';

function msg(botUuid?: string): ChatMessage {
  return { extra: botUuid === undefined ? {} : { botUuid } } as unknown as ChatMessage;
}

describe('collectSenderBots', () => {
  test('归并 assistant 消息发送方并按复合键去重', () => {
    const bots = collectSenderBots([
      msg('dataflow-research:435979'),
      msg('dataflow-research:435979'),
      msg('default:146836'),
    ]);
    expect(bots).toEqual([
      { botId: 'dataflow-research', ownerId: '435979' },
      { botId: 'default', ownerId: '146836' },
    ]);
  });

  test('多 owner 同名 botId 不合并（各自成桶）', () => {
    const bots = collectSenderBots([msg('default:100001'), msg('default:100002')]);
    expect(bots.map(senderBotKey)).toEqual(['default:100001', 'default:100002']);
  });

  test('uuid 缺 owner 段且回退无歧义时，采纳同 botId 的唯一已知 owner', () => {
    const bots = collectSenderBots([
      msg('dataflow-research'), // 无 owner
      msg('dataflow-research:435979'),
    ]);
    expect(bots).toEqual([{ botId: 'dataflow-research', ownerId: '435979' }]);
  });

  test('uuid 缺 owner 且同 botId 多 owner 已知，保持无 owner 形态（不猜）', () => {
    const bots = collectSenderBots([msg('default'), msg('default:100001'), msg('default:100002')]);
    expect(bots).toEqual([
      { botId: 'default' }, // 无 owner 段排序在前（ownerId ?? '' 空串）
      { botId: 'default', ownerId: '100001' },
      { botId: 'default', ownerId: '100002' },
    ]);
  });

  test('跳过人类 actor、BCS 系统消息与非字符串/缺失 uuid', () => {
    const bots = collectSenderBots([
      msg('human_435979'),
      msg(BCS_SYSTEM_MESSAGE_BOT_UUID),
      msg(''),
      { extra: { botUuid: 123 } } as unknown as ChatMessage,
      msg('real-bot:435979'),
    ]);
    expect(bots).toEqual([{ botId: 'real-bot', ownerId: '435979' }]);
  });

  test('输出按 botId/ownerId 排序稳定（增量归并不改变既有次序）', () => {
    const first = collectSenderBots([msg('b-bot:2'), msg('a-bot:1')]);
    const second = collectSenderBots([msg('a-bot:1'), msg('b-bot:2'), msg('a-bot:1')]);
    expect(first).toEqual(second);
    expect(first.map((b) => b.botId)).toEqual(['a-bot', 'b-bot']);
  });
});

test('senderBotKey 复合键与 owner 缺失形态', () => {
  expect(senderBotKey({ botId: 'a' })).toBe('a:');
  expect(senderBotKey({ botId: 'a', ownerId: '1' })).toBe('a:1');
});

describe('collectParticipantBots', () => {
  test('归并 Bot 成员并按复合键去重（actorId 与消息 botUuid 同构）', () => {
    const bots = collectParticipantBots([
      { actorId: 'dataflow-research:435979', kind: 'bot' },
      { actorId: 'dataflow-research:435979', kind: 'bot' },
      { actorId: 'default:100001', kind: 'bot' },
      { actorId: 'default:100002', kind: 'bot' },
    ]);
    expect(bots).toEqual([
      { botId: 'dataflow-research', ownerId: '435979' },
      { botId: 'default', ownerId: '100001' },
      { botId: 'default', ownerId: '100002' },
    ]);
  });

  test('跳过人类成员（kind 显式 human 与 human_ 前缀双保险）与系统消息', () => {
    const bots = collectParticipantBots([
      { actorId: 'human_435979', kind: 'human' },
      { actorId: 'human_435979' }, // 列表接口摘要可能缺 kind，靠 human_ 前缀过滤
      { actorId: BCS_SYSTEM_MESSAGE_BOT_UUID, kind: 'bot' },
      { actorId: '', kind: 'bot' },
      { actorId: 'real-bot:435979', kind: 'bot' },
    ]);
    expect(bots).toEqual([{ botId: 'real-bot', ownerId: '435979' }]);
  });

  test('actorId 缺 owner 段时保持无 owner 形态（参与者 uuid 权威，不猜）', () => {
    const bots = collectParticipantBots([{ actorId: 'ownerless-bot', kind: 'bot' }]);
    expect(bots).toEqual([{ botId: 'ownerless-bot' }]);
    expect(bots.map(senderBotKey)).toEqual(['ownerless-bot:']);
  });
});
