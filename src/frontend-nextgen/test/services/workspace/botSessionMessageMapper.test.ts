import type { BotMessageDto } from '@/services/backendApi/bots/privateBotSessionController';
import { mapBotSessionMessages } from '@/services/workspace/botSessionMessageMapper';
import { describe, expect, it } from '@jest/globals';

describe('mapBotSessionMessages', () => {
  const items: BotMessageDto[] = [
    { message_id: 'm2', session_id: 's', role: 'assistant', content: '你好', gmt_create: '2026-08-14T09:01:00+00:00' },
    { message_id: 'm1', session_id: 's', role: 'user', content: 'hi', gmt_create: '2026-08-14T09:00:00+00:00' },
  ];
  it('返回旧→新升序,role 与 content 映射', () => {
    const out = mapBotSessionMessages(items);
    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({ id: 'm1', role: 'user', content: 'hi', status: 'history' });
    expect(out[1]).toMatchObject({ id: 'm2', role: 'assistant', content: '你好', status: 'history' });
  });
  it('system 消息保留 role=system', () => {
    const out = mapBotSessionMessages([
      { message_id: 's1', session_id: 's', role: 'system', content: 'note', gmt_create: '' },
    ]);
    expect(out[0].role).toBe('system');
  });
  it('未知 role(tool_use/tool_result)跳过', () => {
    const out = mapBotSessionMessages([
      { message_id: 't1', session_id: 's', role: 'tool_use', content: '', gmt_create: '' },
      { message_id: 'u1', session_id: 's', role: 'user', content: 'hi', gmt_create: '' },
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe('u1');
  });
  it('空 content 的 user/assistant 跳过', () => {
    const out = mapBotSessionMessages([
      { message_id: 'e1', session_id: 's', role: 'assistant', content: '', gmt_create: '' },
    ]);
    expect(out).toHaveLength(0);
  });
});
