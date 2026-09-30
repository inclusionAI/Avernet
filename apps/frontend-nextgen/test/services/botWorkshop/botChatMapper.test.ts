import { mapBotChatDetail, mapBotChatPage, maskBotChatSecrets } from '@/services/botWorkshop/botChatMapper';

describe('botChatMapper', () => {
  test('映射分页字段和模型', () => {
    const page = mapBotChatPage({
      sessions: [
        {
          id: 't1',
          timestamp: '2026-08-19T00:00:00Z',
          bot_id: 'source-bot',
          bot_name: '来源 Bot',
          metadata: { attributes: { 'gen_ai.request.model': 'qwen' } },
        },
      ],
      total: 1,
      page: 1,
      limit: 20,
      has_more: false,
    });
    expect(page.items[0]).toMatchObject({
      id: 't1',
      botId: 'source-bot',
      botName: '来源 Bot',
      model: 'qwen',
      totalTokens: 0,
    });
    expect(page.hasMore).toBe(false);
  });

  test('递归映射 observation 并脱敏输入输出', () => {
    const detail = mapBotChatDetail({
      id: 't1',
      timestamp: '2026-08-19T00:00:00Z',
      input: { authorization: 'sensitive-value' },
      output: { nested: { api_key: 'secret' } },
      observations: [{ id: 'o1', type: 'TOOL', input: { password: 'p' }, children: [{ id: 'o2', type: 'LLM' }] }],
    });
    expect(detail.input).toEqual({ authorization: '***' });
    expect(detail.output).toEqual({ nested: { api_key: '***' } });
    expect(detail.observations[0].input).toEqual({ password: '***' });
    expect(detail.observations[0].children[0].id).toBe('o2');
  });

  test('普通 session_key 不会因包含 key 被误脱敏', () => {
    expect(maskBotChatSecrets({ session_key: 'agent:main', token: 'secret' })).toEqual({
      session_key: 'agent:main',
      token: '***',
    });
  });
});
