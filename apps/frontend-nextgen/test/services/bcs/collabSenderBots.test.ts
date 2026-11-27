import type { ChatMessage, TextBlock } from '@tc-chat/core';
import {
  BCS_SYSTEM_MESSAGE_BOT_UUID,
  collectSenderBots,
  senderBotKey,
} from '@/services/bcs/collabSenderBots';

const panel = '<AixUI type="panel" component="lib.View" />';

function msg(botUuid?: string, content = panel): ChatMessage {
  return { role: 'assistant', content, extra: botUuid === undefined ? {} : { botUuid } } as unknown as ChatMessage;
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
      { ...msg(), extra: { botUuid: 123 } } as unknown as ChatMessage,
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

test('只有需要 Bot CDN 的组件消息才注册发送方；Human/系统消息不使用路由 Bot', () => {
  expect(collectSenderBots([
    msg('plain:1', '普通文本'),
    msg('code-block:11', '```html\n' + panel + '\n```'),
    msg('inline-code:12', '`' + panel + '`'),
    msg('comment:14', '<!-- ' + panel + ' -->'),
    msg('indented:15', '    ' + panel),
    msg('tab-indented:17', '\t' + panel),
    { ...msg('tool-output:16'), blocks: [{ type: 'text', content: '实际渲染是普通消息' } as TextBlock] },
    msg('unquoted:13', '<AixUI type=panel component=lib.View />'),
    msg('legacy:2', `<AixUI component="lib.View" panel='{}'></AixUI>`),
    msg('card:3', '<AixUI type="panel" cardId="card-1" />'),
    msg('explicit:4', '<AixUI type="panel" component="lib.View" cdn="/assets/lib.js" />'),
    msg('task:5', '<AixUI type="panel" component="taskPanel.TaskLoopView" />'),
    { ...msg('route:6'), role: 'user' },
    { ...msg('route:7'), role: 'assistant', extra: { senderId: 'human_7', botUuid: 'route:7' } },
    { ...msg('fallback:8'), extra: { senderId: 'real:8', botUuid: 'fallback:8' } },
    msg('partial:9', '<AixUI type="panel" component="lib.View"'),
    msg('inline:10', '<AixUI type="card" component="other.View" />'),
  ])).toEqual([{ botId: 'indented', ownerId: '15' }, { botId: 'inline', ownerId: '10' }, { botId: 'legacy', ownerId: '2' }, { botId: 'real', ownerId: '8' }, { botId: 'unquoted', ownerId: '13' }]);
});

test('检查实际 text block，并利用同批普通消息里的明确 owner 做唯一归属回退', () => {
  expect(collectSenderBots([
    { ...msg('bot-a', ''), blocks: [{ type: 'text', content: panel } as TextBlock] },
    msg('bot-a:1', '普通文本'),
    msg('bot-b:2', '普通文本'),
  ])).toEqual([{ botId: 'bot-a', ownerId: '1' }]);
});

const multilinePanel = `<AixUI type="panel" component="lib.View" params='{

  "value": 1
}' />`;

test.each([
  multilinePanel,
  '<AixUI\n\n type="panel" component="lib.View" />',
  multilinePanel.replace(/\n/g, '\r\n'),
  multilinePanel.split('\n').map((line) => `    ${line}`).join('\n'),
  '> <AixUI\n>\n>  type="panel" component="lib.View" />',
  '- <AixUI\n\n   type="panel" component="lib.View" />',
  '<AixUI params=\'{\n\n"comparison": "a > b"\n}\' component="lib.View" type="panel" />',
])('跨空行的 AixUI 开标签仍为发送方准备 CDN：%s', (content) => {
  expect(collectSenderBots([msg('bot-a:1', content)])).toEqual([{ botId: 'bot-a', ownerId: '1' }]);
});

test.each([
  `\`\`\`html\n${multilinePanel}\n\`\`\``,
  `\`${multilinePanel}\``,
  `<!--\n${multilinePanel}\n-->`,
])('跨空行的代码示例和注释不加载 CDN：%s', (content) => {
  expect(collectSenderBots([msg('bot-a:1', content)])).toEqual([]);
});
