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

  it('groups assistant messages with the same run_id into one ChatMessage', () => {
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

  it('hydrates one run even when another bot run is interleaved', () => {
    const items = mapGroupHistoryMessages([
      {
        id: 'a1',
        timestamp: 1,
        sender: 'bot-a',
        content: 'a-db',
        message_type: 'bot',
        role: 'assistant',
        run_id: 'run-a',
      },
      {
        id: 'b1',
        timestamp: 2,
        sender: 'bot-b',
        content: 'b',
        message_type: 'bot',
        role: 'assistant',
        run_id: 'run-b',
      },
      {
        id: 'a2',
        timestamp: 3,
        sender: 'bot-a',
        content: 'a-pending',
        message_type: 'bot',
        role: 'assistant',
        run_id: 'run-a',
        metadata: { bcs_pending: true, pending_kind: 'chat' },
      },
    ] as GroupHistoryDto[]);

    expect(items).toHaveLength(2);
    expect(items[0]).toMatchObject({ content: 'a-db\na-pending', status: 'streaming' });
    expect(items[1]).toMatchObject({ content: 'b', status: 'history' });
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

  it('merges tracker pending text into the durable run and marks it streaming', () => {
    const items = mapGroupHistoryMessages([
      {
        id: 'db-1',
        timestamp: 1,
        sender: 'bot-a',
        content: '已落库段',
        message_type: 'bot',
        role: 'assistant',
        run_id: 'run-1',
      },
      {
        id: 'bcs-run:run-1:bot-a',
        timestamp: 2,
        sender: 'bot-a',
        content: '未落库段',
        message_type: 'bot',
        role: 'assistant',
        run_id: 'run-1',
        metadata: { bcs_pending: true, pending_kind: 'chat' },
      },
    ] as GroupHistoryDto[]);

    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ content: '已落库段\n未落库段', status: 'streaming' });
    expect(items[0].extra).toMatchObject({ runId: 'run-1', botUuid: 'bot-a', bcsPending: true });
  });

  it('maps a pending tool call to a running tool block', () => {
    const items = mapGroupHistoryMessages([
      {
        id: 'bcs-run:run-1:bot-a',
        timestamp: 1,
        sender: 'bot-a',
        content: '',
        message_type: 'bot',
        role: 'assistant',
        run_id: 'run-1',
        metadata: {
          bcs_pending: true,
          pending_kind: 'tool_call',
          tool_call_id: 'call-1',
          tool_name: 'search',
          tool_args: { q: 'pending' },
        },
      },
    ] as GroupHistoryDto[]);

    expect(items[0].status).toBe('streaming');
    expect(items[0].blocks?.[0]).toMatchObject({
      type: 'tool_execution',
      steps: [{ id: 'call-1', tool: 'search', status: 'running' }],
    });
  });

  it('keeps a pending tool after intervening text so WS replay can hydrate the open block', () => {
    const items = mapGroupHistoryMessages([
      {
        id: 'text-1',
        timestamp: 1,
        sender: 'bot-a',
        content: '先检查环境',
        message_type: 'bot',
        role: 'assistant',
        run_id: 'run-1',
      },
      {
        id: 'tool-1',
        timestamp: 2,
        sender: 'bot-a',
        content: 'ok',
        message_type: 'bot',
        role: 'tool_result',
        run_id: 'run-1',
        metadata: {
          tool_call_id: 'call-old',
          tool_name: 'Bash',
          arguments: { command: 'which mcporter' },
          result: 'ok',
          is_error: false,
        },
      },
      {
        id: 'text-2',
        timestamp: 3,
        sender: 'bot-a',
        content: '继续列出服务',
        message_type: 'bot',
        role: 'assistant',
        run_id: 'run-1',
      },
      {
        id: 'bcs-run:run-1:bot-a',
        timestamp: 4,
        sender: 'bot-a',
        content: '',
        message_type: 'bot',
        role: 'assistant',
        run_id: 'run-1',
        metadata: {
          bcs_pending: true,
          pending_kind: 'tool_call',
          tool_call_id: 'call-pending',
          tool_name: 'Bash',
          tool_args: { command: 'mcporter list' },
        },
      },
    ] as GroupHistoryDto[]);

    expect(items).toHaveLength(1);
    expect(items[0].blocks?.map((block) => block.type)).toEqual(['text', 'tool_execution', 'text', 'tool_execution']);
    expect(items[0].blocks?.[3]).toMatchObject({
      type: 'tool_execution',
      steps: [{ id: 'call-pending', status: 'running' }],
    });
  });

  it('updates the same tool_call_id instead of keeping tracker and DB copies', () => {
    const items = mapGroupHistoryMessages([
      {
        id: 'bcs-run:run-1:bot-a',
        timestamp: 1,
        sender: 'bot-a',
        content: '',
        message_type: 'bot',
        role: 'assistant',
        run_id: 'run-1',
        metadata: {
          bcs_pending: true,
          pending_kind: 'tool_call',
          tool_call_id: 'call-1',
          tool_name: 'Bash',
          tool_args: { command: 'mcporter list' },
        },
      },
      {
        id: 'tool-result',
        timestamp: 2,
        sender: 'bot-a',
        content: 'done',
        message_type: 'bot',
        role: 'tool_result',
        run_id: 'run-1',
        metadata: {
          tool_call_id: 'call-1',
          tool_name: 'Bash',
          arguments: { command: 'mcporter list' },
          result: 'done',
          is_error: false,
        },
      },
    ] as GroupHistoryDto[]);

    const toolBlocks = items[0].blocks?.filter((block) => block.type === 'tool_execution') ?? [];
    expect(toolBlocks).toHaveLength(1);
    expect(toolBlocks[0]).toMatchObject({
      steps: [{ id: 'call-1', status: 'success', output: 'done' }],
    });
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
