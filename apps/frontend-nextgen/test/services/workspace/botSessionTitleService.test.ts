import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { buildFirstMessageSessionTitle } from '@/services/workspace/botSessionTitleService';
import { describe, expect, it } from '@jest/globals';

const session = (overrides: Partial<BotChatSessionView> = {}): BotChatSessionView => ({
  sessionId: 'session-1',
  botId: 'bot-1:owner-1',
  title: '新会话',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
  ...overrides,
});

describe('buildFirstMessageSessionTitle', () => {
  it('标题为新会话且 messageCount 为 0 时使用首条消息生成标题', () => {
    expect(buildFirstMessageSessionTitle(session(), '  帮我整理本周项目进展  ')).toBe('帮我整理本周项目进展');
  });

  it('标题不是新会话或 messageCount 大于 0 时不再自动生成标题', () => {
    expect(buildFirstMessageSessionTitle(session({ title: '已手动命名' }), '第一条消息')).toBeNull();
    expect(buildFirstMessageSessionTitle(session({ messageCount: 1 }), '第二条消息')).toBeNull();
  });

  it('空白消息不生成标题，超长消息截断前 50 字符并追加省略号', () => {
    const longMessage = '一二三四五六七八九十'.repeat(6);
    expect(buildFirstMessageSessionTitle(session(), '   ')).toBeNull();
    expect(buildFirstMessageSessionTitle(session(), longMessage)).toBe(`${longMessage.slice(0, 50)}...`);
  });

  // OpenSpec fix-session-title-auto-rename §session-first-message-title：
  // CodingBot 建会话写 `新对话`，守卫此前只认 `新会话` → marker 错配永不自动命名。
  it('CodingBot 默认标题「新对话」也被守卫接受（marker 集合化）', () => {
    const codingSession = session({ title: '新对话', messageCount: 0 });
    expect(buildFirstMessageSessionTitle(codingSession, '帮我写个排序算法')).toBe('帮我写个排序算法');
  });
});
