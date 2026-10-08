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

  it('同一轮连续多个 tool call 聚合为一个工具执行消息', () => {
    const out = mapBotSessionMessages([
      { message_id: 'u1', session_id: 's', role: 'user', content: '请处理', gmt_create: '2026-09-15T01:00:00+00:00' },
      {
        message_id: 'read-use',
        session_id: 's',
        role: 'tool_use',
        content: '',
        gmt_create: '2026-09-15T01:00:01+00:00',
        metadata: { tool_call_id: 'call-read', tool_name: 'read', arguments: { path: 'a.ts' } },
      },
      {
        message_id: 'read-result',
        session_id: 's',
        role: 'tool_result',
        content: '',
        gmt_create: '2026-09-15T01:00:02+00:00',
        metadata: { tool_call_id: 'call-read', tool_name: 'read', result: 'content' },
      },
      {
        message_id: 'write-use',
        session_id: 's',
        role: 'tool_use',
        content: '',
        gmt_create: '2026-09-15T01:00:03+00:00',
        metadata: { tool_call_id: 'call-write', tool_name: 'write', arguments: { path: 'a.ts' } },
      },
      {
        message_id: 'write-result',
        session_id: 's',
        role: 'tool_result',
        content: '',
        gmt_create: '2026-09-15T01:00:04+00:00',
        metadata: { tool_call_id: 'call-write', tool_name: 'write', result: 'ok' },
      },
    ]);

    expect(out).toHaveLength(2);
    expect(out[1]).toMatchObject({ id: 'read-use', role: 'assistant', status: 'history' });
    expect(out[1].blocks).toEqual([
      {
        type: 'tool_execution',
        steps: [
          expect.objectContaining({ id: 'call-read', tool: 'read', status: 'success', output: 'content' }),
          expect.objectContaining({ id: 'call-write', tool: 'write', status: 'success', output: 'ok' }),
        ],
      },
    ]);
  });

  it('同一 conversationRoundId 的文本与多个工具按原始顺序聚合为一条 assistant 消息', () => {
    const withRound = (message: BotMessageDto): BotMessageDto =>
      ({ ...message, history_meta: { conversationRoundId: 'round-1' } } as BotMessageDto);
    const out = mapBotSessionMessages([
      withRound({
        message_id: 'u1',
        session_id: 's',
        role: 'user',
        content: '请修改文件',
        gmt_create: '2026-09-15T02:00:00+00:00',
      }),
      withRound({
        message_id: 'a1',
        session_id: 's',
        role: 'assistant',
        content: '我先读取。',
        gmt_create: '2026-09-15T02:00:01+00:00',
      }),
      withRound({
        message_id: 'read-result',
        session_id: 's',
        role: 'tool_result',
        content: '',
        gmt_create: '2026-09-15T02:00:02+00:00',
        metadata: { tool_call_id: 'call-read', tool_name: 'read', result: 'old' },
      }),
      withRound({
        message_id: 'a2',
        session_id: 's',
        role: 'assistant',
        content: '然后写入。',
        gmt_create: '2026-09-15T02:00:03+00:00',
      }),
      withRound({
        message_id: 'write-result',
        session_id: 's',
        role: 'tool_result',
        content: '',
        gmt_create: '2026-09-15T02:00:04+00:00',
        metadata: { tool_call_id: 'call-write', tool_name: 'write', result: 'ok' },
      }),
      withRound({
        message_id: 'a3',
        session_id: 's',
        role: 'assistant',
        content: '处理完成。',
        gmt_create: '2026-09-15T02:00:05+00:00',
      }),
    ]);

    expect(out).toHaveLength(2);
    expect(out[1]).toMatchObject({ id: 'a1', role: 'assistant', content: '我先读取。然后写入。处理完成。' });
    expect(out[1].blocks).toEqual([
      { type: 'text', content: '我先读取。' },
      { type: 'tool_execution', steps: [expect.objectContaining({ id: 'call-read', output: 'old' })] },
      { type: 'text', content: '然后写入。' },
      { type: 'tool_execution', steps: [expect.objectContaining({ id: 'call-write', output: 'ok' })] },
      { type: 'text', content: '处理完成。' },
    ]);
  });

  it('工具消息缺少 roundId 时可由随后同轮 assistant 文本补齐并聚合', () => {
    const out = mapBotSessionMessages([
      { message_id: 'u1', session_id: 's', role: 'user', content: '查询状态', gmt_create: '2026-09-15T03:00:00+00:00' },
      {
        message_id: 'tool-use',
        session_id: 's',
        role: 'tool_use',
        content: '',
        gmt_create: '2026-09-15T03:00:01+00:00',
        metadata: { tool_call_id: 'call-1', tool_name: 'status' },
      },
      {
        message_id: 'tool-result',
        session_id: 's',
        role: 'tool_result',
        content: '',
        gmt_create: '2026-09-15T03:00:02+00:00',
        metadata: { tool_call_id: 'call-1', tool_name: 'status', result: 'ready' },
      },
      {
        message_id: 'assistant-final',
        session_id: 's',
        role: 'assistant',
        content: '当前已就绪。',
        gmt_create: '2026-09-15T03:00:03+00:00',
        history_meta: { conversationRoundId: 'round-1' },
      },
    ]);

    expect(out).toHaveLength(2);
    expect(out[1]).toMatchObject({ id: 'tool-use', role: 'assistant', content: '当前已就绪。' });
    expect(out[1].blocks?.map((block) => block.type)).toEqual(['tool_execution', 'text']);
    expect(out[1].extra).toMatchObject({ conversationRoundId: 'round-1', runId: 'round-1' });
  });

  it('即使 user 内容为空也会切断前后工具消息的聚合', () => {
    const out = mapBotSessionMessages([
      {
        message_id: 'tool-1',
        session_id: 's',
        role: 'tool_result',
        content: '',
        gmt_create: '2026-09-15T04:00:00+00:00',
        metadata: { tool_call_id: 'call-1', tool_name: 'read', result: 'one' },
      },
      { message_id: 'user-empty', session_id: 's', role: 'user', content: '', gmt_create: '2026-09-15T04:00:01+00:00' },
      {
        message_id: 'tool-2',
        session_id: 's',
        role: 'tool_result',
        content: '',
        gmt_create: '2026-09-15T04:00:02+00:00',
        metadata: { tool_call_id: 'call-2', tool_name: 'write', result: 'two' },
      },
    ]);

    expect(out).toHaveLength(2);
    expect(out[0].blocks?.[0]).toMatchObject({
      type: 'tool_execution',
      steps: [expect.objectContaining({ id: 'call-1' })],
    });
    expect(out[1].blocks?.[0]).toMatchObject({
      type: 'tool_execution',
      steps: [expect.objectContaining({ id: 'call-2' })],
    });
  });

  it('不同 run_id 即使复用 tool_call_id 也不会串到同一条消息', () => {
    const out = mapBotSessionMessages([
      {
        message_id: 'tool-round-1',
        session_id: 's',
        role: 'tool_result',
        content: '',
        gmt_create: '2026-09-15T05:00:00+00:00',
        run_id: 'round-1',
        metadata: { tool_call_id: 'call-shared', tool_name: 'read', result: 'one' },
      },
      {
        message_id: 'tool-round-2',
        session_id: 's',
        role: 'tool_result',
        content: '',
        gmt_create: '2026-09-15T05:00:01+00:00',
        run_id: 'round-2',
        metadata: { tool_call_id: 'call-shared', tool_name: 'read', result: 'two' },
      },
    ]);

    expect(out).toHaveLength(2);
    expect((out[0].blocks?.[0] as unknown as { steps: Array<{ output?: string }> }).steps[0].output).toBe('one');
    expect((out[1].blocks?.[0] as unknown as { steps: Array<{ output?: string }> }).steps[0].output).toBe('two');
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
