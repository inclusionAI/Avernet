import { getDebugMessageBlocks, shouldAppendThinking } from '@/components/BotWorkshop/Editor/DebugChatPanel';

describe('DebugChatPanel thinking state', () => {
  test('发送后尚无助手占位消息时追加 thinking', () => {
    expect(shouldAppendThinking([{ role: 'user', content: '你好' }], true)).toBe(true);
  });

  test('已有助手消息或请求结束时不重复追加 thinking', () => {
    expect(shouldAppendThinking([{ role: 'assistant', content: '' }], true)).toBe(false);
    expect(shouldAppendThinking([{ role: 'user', content: '你好' }], false)).toBe(false);
  });

  test('保留 SDK 输出的结构化 thinking 和工具块，而非只取最终正文', () => {
    const blocks = [
      { type: 'thinking' as const, content: '正在检查配置', isLoading: true },
      { type: 'tool_execution' as const, steps: [] },
      { type: 'text' as const, content: '最终回复' },
    ];

    expect(getDebugMessageBlocks({ role: 'assistant', content: '最终回复', blocks })).toBe(blocks);
  });

  test('没有结构化块时回退为文本块', () => {
    expect(getDebugMessageBlocks({ role: 'assistant', content: '最终回复' })).toEqual([
      { type: 'text', content: '最终回复' },
    ]);
  });
});
