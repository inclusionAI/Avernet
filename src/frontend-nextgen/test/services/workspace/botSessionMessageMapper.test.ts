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
  it('tool_use 与同 call_id 的 tool_result 合并为历史工具卡片', () => {
    const out = mapBotSessionMessages([
      { message_id: 'u1', session_id: 's', role: 'user', content: 'hi', gmt_create: '2026-08-14T09:00:00+00:00' },
      {
        message_id: 't1',
        session_id: 's',
        role: 'tool_use',
        content: '',
        gmt_create: '2026-08-14T09:01:00+00:00',
        metadata: { tool_call_id: 'call-1', tool_name: 'search', arguments: { query: 'teamclaw' } },
      },
      {
        message_id: 'tr1',
        session_id: 's',
        role: 'tool_result',
        content: '',
        gmt_create: '2026-08-14T09:02:00+00:00',
        metadata: { tool_call_id: 'call-1', tool_name: 'search', result: 'found' },
      },
      {
        message_id: 'a1',
        session_id: 's',
        role: 'assistant',
        content: 'done',
        gmt_create: '2026-08-14T09:03:00+00:00',
      },
    ]);

    expect(out).toHaveLength(3);
    expect(out[0]).toMatchObject({ id: 'u1', role: 'user' });
    expect(out[1]).toMatchObject({ id: 't1', role: 'assistant', content: '', status: 'history' });
    expect(out[1].blocks?.[0]).toMatchObject({
      type: 'tool_execution',
      steps: [
        {
          id: 'call-1',
          tool: 'search',
          title: 'search',
          status: 'success',
          input: '{\n  "query": "teamclaw"\n}',
          output: 'found',
        },
      ],
    });
    expect(out[2]).toMatchObject({ id: 'a1', role: 'assistant', content: 'done' });
  });

  it('tool_result 可独立成卡片并识别错误状态', () => {
    const out = mapBotSessionMessages([
      {
        message_id: 'tr1',
        session_id: 's',
        role: 'tool_result',
        content: 'boom',
        gmt_create: '',
        metadata: { tool_call_id: 'call-1', tool_name: 'bash', success: false },
      },
    ]);

    expect(out).toHaveLength(1);
    expect(out[0].blocks?.[0]).toMatchObject({
      type: 'tool_execution',
      steps: [{ id: 'call-1', tool: 'bash', status: 'error', output: 'boom' }],
    });
  });
  it('空 content 的 user/assistant 跳过', () => {
    const out = mapBotSessionMessages([
      { message_id: 'e1', session_id: 's', role: 'assistant', content: '', gmt_create: '' },
    ]);
    expect(out).toHaveLength(0);
  });
});
