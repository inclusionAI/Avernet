import type { GroupHistoryDto } from '@/services/workspace/groupMessageMapper';
import { mapGroupHistoryMessages, toolResultToToolStep } from '@/services/workspace/groupMessageMapper';
import { describe, expect, it } from '@jest/globals';

describe('groupMessageMapper', () => {
  it('system message maps to role=system, kept as standalone message', () => {
    const items = mapGroupHistoryMessages([
      {
        id: 'm1',
        timestamp: 1700000000000,
        sender: 'bcs-system-message',
        content: 'user 加入协作群',
        message_type: 'system',
        role: 'system',
      },
    ]);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ role: 'system', content: 'user 加入协作群', status: 'history' });
    expect(items[0].createdAt).toBe(1700000000000);
  });

  it('bot → assistant, human → user; bot_name carried in extra', () => {
    const items = mapGroupHistoryMessages([
      {
        id: 'm1',
        timestamp: 1,
        sender: 'bot-a',
        content: 'one',
        message_type: 'bot',
        bot_name: '波士顿龙虾',
        role: 'assistant',
        run_id: 'r1',
      },
      { id: 'm2', timestamp: 2, sender: 'user-1', content: 'two', message_type: 'human', role: 'user' },
      {
        id: 'm3',
        timestamp: 3,
        sender: 'bot-a',
        content: 'three',
        message_type: 'bot',
        bot_name: '波士顿龙虾',
        role: 'assistant',
        run_id: 'r2',
      },
    ]);
    expect(items.map((m) => m.role)).toEqual(['assistant', 'user', 'assistant']);
    expect(items[0].extra?.botName).toBe('波士顿龙虾');
    expect(items[0].extra?.senderId).toBe('bot-a');
  });

  it('unknown message_type and role is dropped, does not throw', () => {
    const items = mapGroupHistoryMessages([
      { id: 'mx', timestamp: 1, sender: 'x', content: 'x', message_type: 'ghost' as never } as never,
      { id: 'm2', timestamp: 2, sender: 'b', content: 'ok', message_type: 'bot', role: 'assistant' },
    ]);
    expect(items.map((m) => m.content)).toEqual(['ok']);
  });

  it('groups consecutive assistant messages with same run_id into one ChatMessage', () => {
    const items = mapGroupHistoryMessages([
      {
        id: 'm1',
        timestamp: 1,
        sender: 'bot-a',
        content: 'p1',
        message_type: 'bot',
        bot_name: 'B',
        role: 'assistant',
        run_id: 'r1',
      },
      {
        id: 'm2',
        timestamp: 2,
        sender: 'bot-a',
        content: 'p2',
        message_type: 'bot',
        bot_name: 'B',
        role: 'assistant',
        run_id: 'r1',
      },
      {
        id: 'm3',
        timestamp: 3,
        sender: 'bot-a',
        content: 'p3',
        message_type: 'bot',
        bot_name: 'B',
        role: 'assistant',
        run_id: 'r1',
      },
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].role).toBe('assistant');
    expect(items[0].content).toBe('p1\np2\np3');
    expect(items[0].extra?.conversationRoundId).toBe('r1');
  });

  it('aggregated assistant round uses the FIRST part senderId/botName', () => {
    const items = mapGroupHistoryMessages([
      {
        id: 'm1',
        timestamp: 1,
        sender: 'bot-a',
        content: 'p1',
        message_type: 'bot',
        bot_name: 'Bot甲',
        role: 'assistant',
        run_id: 'r1',
      },
      {
        id: 'm2',
        timestamp: 2,
        sender: 'bot-a',
        content: 'p2',
        message_type: 'bot',
        bot_name: 'Bot甲',
        role: 'assistant',
        run_id: 'r1',
      },
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].extra?.senderId).toBe('bot-a');
    expect(items[0].extra?.botName).toBe('Bot甲');
  });

  it('does not aggregate assistant messages with different run_id', () => {
    const items = mapGroupHistoryMessages([
      { id: 'm1', timestamp: 1, sender: 'bot-a', content: 'p1', message_type: 'bot', role: 'assistant', run_id: 'r1' },
      { id: 'm2', timestamp: 2, sender: 'bot-a', content: 'p2', message_type: 'bot', role: 'assistant', run_id: 'r2' },
    ]);
    expect(items).toHaveLength(2);
  });

  it('toolResultToToolStep marks error case on success===false', () => {
    const step = toolResultToToolStep({
      id: 'tr1',
      timestamp: 1,
      sender: 'bot-a',
      content: '',
      message_type: 'bot',
      role: 'assistant',
      metadata: { tool_name: 'search', tool_call_id: 'call-1', arguments: { q: 'x' }, success: false, result: 'fail' },
    } as GroupHistoryDto);
    expect(step).toMatchObject({ tool: 'search', id: 'call-1', status: 'error', output: 'fail' });
  });
});

describe('attachments', () => {
  it('converts BCS image attachments to ImageBlock at the top of blocks', () => {
    const items = mapGroupHistoryMessages([
      {
        id: 'm1',
        timestamp: 1,
        sender: 'user-1',
        content: '看这张图',
        message_type: 'human',
        role: 'user',
        attachments: [
          {
            attachment_id: 'att-1',
            type: 'image',
            file_name: '截图.png',
            mime_type: 'image/png',
            size: 1024,
            url: 'https://cdn.example.com/share/x.png',
            expires_at: 1800000000000,
          },
        ],
      },
    ]);
    expect(items).toHaveLength(1);
    const blocks = items[0].blocks ?? [];
    // 图片置顶，文本在下
    expect(blocks[0]).toMatchObject({ type: 'image', data: 'https://cdn.example.com/share/x.png', name: '截图.png' });
    expect(blocks.some((b) => b.type === 'text' && (b as { content?: string }).content === '看这张图')).toBe(true);
  });

  it('有 sessionId 时图片附件改走同源会话内容地址,避免分享域名跨域', () => {
    const items = mapGroupHistoryMessages(
      [
        {
          id: 'm1',
          timestamp: 1,
          sender: 'user-1',
          content: '看这张图',
          message_type: 'human',
          role: 'user',
          attachments: [
            {
              attachment_id: 'att-1',
              type: 'image',
              file_name: '截图.png',
              mime_type: 'image/png',
              url: 'https://cdn.example.com/share/x.png',
            },
          ],
        },
      ],
      's1',
    );
    const blocks = items[0].blocks ?? [];
    expect(blocks[0]).toMatchObject({
      type: 'image',
      data: '/api/v1/collaboration/sessions/s1/files/att-1/content?show=true',
    });
  });

  it('shows placeholder when image attachment has no url', () => {
    const items = mapGroupHistoryMessages([
      {
        id: 'm1',
        timestamp: 1,
        sender: 'user-1',
        content: '',
        message_type: 'human',
        role: 'user',
        attachments: [{ attachment_id: 'att-2', type: 'image', file_name: 'gone.jpg', url: '' }],
      },
    ]);
    const blocks = items[0].blocks ?? [];
    const img = blocks.find((b) => b.type === 'image') as { type: string; data: string } | undefined;
    expect(img).toBeDefined();
    expect(img!.data).toContain('data:image/svg+xml');
  });

  it('message without attachments only has text block (no image block)', () => {
    const items = mapGroupHistoryMessages([
      { id: 'm1', timestamp: 1, sender: 'bot-a', content: '纯文本', message_type: 'bot', role: 'assistant' },
    ]);
    const blocks = items[0].blocks ?? [];
    expect(blocks.some((b) => b.type === 'image')).toBe(false);
  });
});
