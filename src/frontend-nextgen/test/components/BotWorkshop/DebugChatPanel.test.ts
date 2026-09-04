import { shouldAppendThinking } from '@/components/BotWorkshop/Editor/DebugChatPanel';

describe('DebugChatPanel thinking state', () => {
  test('发送后尚无助手占位消息时追加 thinking', () => {
    expect(shouldAppendThinking([{ role: 'user', content: '你好' }], true)).toBe(true);
  });

  test('已有助手消息或请求结束时不重复追加 thinking', () => {
    expect(shouldAppendThinking([{ role: 'assistant', content: '' }], true)).toBe(false);
    expect(shouldAppendThinking([{ role: 'user', content: '你好' }], false)).toBe(false);
  });
});
