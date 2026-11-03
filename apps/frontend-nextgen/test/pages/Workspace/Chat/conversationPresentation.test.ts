import { resolveConversationPresentation } from '@/pages/Workspace/Chat/conversationPresentation';
import type { ChatBotView } from '@/services/workspace/botSessionService';

const botOf = (overrides: Partial<ChatBotView> = {}): ChatBotView => ({
  botId: 'bot-1:100',
  realBotId: 'bot-1',
  ownerId: '100',
  displayName: 'Bot',
  online: true,
  chatable: true,
  ...overrides,
});

const unresolved = { resolved: false, engine: null };

describe('resolveConversationPresentation', () => {
  it('普通 Bot 使用完整标准会话能力', () => {
    expect(resolveConversationPresentation(botOf())).toEqual(
      expect.objectContaining({
        variant: 'standard',
        composerExtensions: true,
        taskComposer: true,
        modelSelector: true,
        fileFeatures: true,
        resourceManager: true,
        sidePane: true,
      }),
    );
  });

  it('好友 Bot 在 connection 未返回且 metadata 无法确认时使用保守 pending 模式', () => {
    expect(
      resolveConversationPresentation(
        botOf({ isFriendBot: true, engine: 'claude_code', templateType: 'normal' }),
        unresolved,
      ),
    ).toEqual({
      variant: 'friend-runtime-pending',
      composerPlaceholder: '连接 Bot 后即可开始对话',
      composerExtensions: false,
      taskComposer: false,
      modelSelector: false,
      fileFeatures: false,
      resourceManager: false,
      sidePane: false,
    });
  });

  it.each([
    { engine: 'aicoding' },
    { engine: 'applicationcoding' },
    { engine: 'claude_code', templateType: 'application_coding' },
  ])('connection 未返回时，metadata 明确的好友 Coding Bot 可提前进入兼容模式', (runtime) => {
    expect(resolveConversationPresentation(botOf({ isFriendBot: true, ...runtime }), unresolved).variant).toBe(
      'friend-coding-compat',
    );
  });

  it.each(['aicoding', 'application_coding'])('connection=%s 权威覆盖普通 CC metadata', (engine) => {
    const presentation = resolveConversationPresentation(
      botOf({ isFriendBot: true, engine: 'claude_code', templateType: 'normal' }),
      { resolved: true, engine },
    );
    expect(presentation.variant).toBe('friend-coding-compat');
    expect(presentation.composerPlaceholder).toBe('请输入你的问题或任务');
  });

  it('connection=claude_code 权威覆盖过期的 Coding metadata并恢复完整能力', () => {
    expect(
      resolveConversationPresentation(botOf({ isFriendBot: true, engine: 'aicoding' }), {
        resolved: true,
        engine: 'claude_code',
      }),
    ).toEqual(
      expect.objectContaining({
        variant: 'standard',
        composerExtensions: true,
        taskComposer: true,
        modelSelector: true,
        fileFeatures: true,
        resourceManager: true,
        sidePane: true,
      }),
    );
  });

  it('非好友 Coding Bot 不由兼容展示模式接管', () => {
    expect(resolveConversationPresentation(botOf({ engine: 'aicoding', isAgentCodingBot: true })).variant).toBe(
      'standard',
    );
  });
});
