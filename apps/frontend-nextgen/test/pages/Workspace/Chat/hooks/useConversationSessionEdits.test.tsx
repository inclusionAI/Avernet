/** @jest-environment jsdom */
// useConversationSessionEdits 直测:首条消息自动重命名(成功/失败/单次尝试/无标题)、
// 清除上下文(成功/失败 toast)、模型切换,以及 conversationStore 缓存回写门禁
// (管理 Bot 缓存仅 origin=mine 回写;好友 Bot 缓存回写)。
import { useConversationSessionEdits } from '@/pages/Workspace/Chat/hooks/useConversationSessionEdits';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { conversationInitialState, useConversationStore } from '@/stores/conversationStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { renderHook } from '@testing-library/react';
import { toast } from 'sonner';

// spy 于既有 singleton 对象,保留其余方法真实实现(无需整模块 mock)。
const updateSessionTitle = jest.spyOn(botSessionService, 'updateSessionTitle');
const clearContextMock = jest.spyOn(botSessionService, 'clearContext');
const toastInfoSpy = jest.spyOn(toast, 'info');
const toastSuccessSpy = jest.spyOn(toast, 'success');
const toastErrorSpy = jest.spyOn(toast, 'error');
beforeEach(() => {
  updateSessionTitle.mockReset();
  clearContextMock.mockReset();
  toastInfoSpy.mockClear();
  toastSuccessSpy.mockClear();
  toastErrorSpy.mockClear();
  jest.spyOn(console, 'warn').mockImplementation(() => {});
});

const bot: ChatBotView = {
  botId: 'bot-a:1',
  realBotId: 'bot-a',
  ownerId: '1',
  displayName: '管理 Bot',
  online: true,
  chatable: true,
};
const baseSession: BotChatSessionView = {
  sessionId: 's1',
  botId: 'bot-a:1',
  title: '新会话',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
};
const emptyList = {
  items: [baseSession],
  page: 1,
  total: 1,
  hasMore: false,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
};

function seedMineCache(session: BotChatSessionView = baseSession) {
  useConversationStore.setState({
    sessionsByBotId: {
      'bot-a:1': {
        botId: 'bot-a:1',
        origin: 'mine',
        scope: 'all',
        sessions: { ...emptyList, items: [{ ...session }] },
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
      },
    },
  });
}

function seededSession(): BotChatSessionView {
  const session = useConversationStore.getState().sessionsByBotId['bot-a:1']!.sessions.items[0];
  if (!session) throw new Error('缓存缺失');
  return session;
}

function renderEdits(userId: string | null = '101') {
  return renderHook(() => useConversationSessionEdits(userId));
}

beforeEach(() => {
  useConversationStore.setState({ ...conversationInitialState });
});

describe('useConversationSessionEdits renameSessionOnFirstMessage', () => {
  it('成功:调用服务并回写缓存中的会话标题', async () => {
    seedMineCache();
    updateSessionTitle.mockResolvedValue({ ok: true, data: { sessionId: 's1', title: 'hello' } });

    const { result } = renderEdits();
    await expect(result.current.renameSessionOnFirstMessage(bot, baseSession, ' hello ')).resolves.toBe(true);

    expect(updateSessionTitle).toHaveBeenCalledWith(bot, '101', 's1', 'hello');
    expect(seededSession().title).toBe('hello');
  });

  it('服务失败:false + console.warn + 标题不回写', async () => {
    seedMineCache();
    updateSessionTitle.mockResolvedValue({
      ok: false,
      error: { code: 'X', friendlyMessage: '重命名失败', canRetry: true },
    });

    const { result } = renderEdits();
    await expect(result.current.renameSessionOnFirstMessage(bot, baseSession, ' hello ')).resolves.toBe(false);

    expect(seededSession().title).toBe('新会话');
    expect((console.warn as jest.Mock).mock.calls[0]?.[0]).toContain('首条消息自动重命名失败');
  });

  it('同一(bot, session)只自动重命名一次', async () => {
    seedMineCache();
    updateSessionTitle.mockResolvedValue({ ok: true, data: { sessionId: 's1', title: 'hello' } });

    const { result } = renderEdits();
    await result.current.renameSessionOnFirstMessage(bot, baseSession, 'hello');
    await result.current.renameSessionOnFirstMessage(bot, baseSession, 'hello again');

    expect(updateSessionTitle).toHaveBeenCalledTimes(1);
  });

  it('非「新会话」或已有消息的会话不触发(无标题产出,不调服务)', async () => {
    const renamed = { ...baseSession, title: '手动命名', messageCount: 3 };
    seedMineCache(renamed);

    const { result } = renderEdits();
    await expect(result.current.renameSessionOnFirstMessage(bot, renamed, 'hello')).resolves.toBe(false);
    expect(updateSessionTitle).not.toHaveBeenCalled();
  });

  it('无登录用户:直接返回 false,不发请求', async () => {
    const { result } = renderEdits(null);
    await expect(result.current.renameSessionOnFirstMessage(bot, baseSession, 'hello')).resolves.toBe(false);
    expect(updateSessionTitle).not.toHaveBeenCalled();
  });
});

describe('useConversationSessionEdits clearContext', () => {
  it('成功:回写 messageCount=0 并 toast 成功', async () => {
    seedMineCache({ ...baseSession, messageCount: 5 });
    clearContextMock.mockResolvedValue({ ok: true, data: null });

    const { result } = renderEdits();
    await expect(result.current.clearContext(bot, 's1')).resolves.toBe(true);

    expect(clearContextMock).toHaveBeenCalledWith(bot, '101', 's1');
    expect(seededSession().messageCount).toBe(0);
    expect(toastSuccessSpy).toHaveBeenCalledWith('会话上下文已清除');
  });

  it('失败:false + toast 错误,缓存不变', async () => {
    seedMineCache({ ...baseSession, messageCount: 5 });
    clearContextMock.mockResolvedValue({
      ok: false,
      error: { code: 'X', friendlyMessage: '清除失败', canRetry: true },
    });

    const { result } = renderEdits();
    await expect(result.current.clearContext(bot, 's1')).resolves.toBe(false);

    expect(seededSession().messageCount).toBe(5);
    expect(toastErrorSpy).toHaveBeenCalledWith('清除失败');
    expect(toastSuccessSpy).not.toHaveBeenCalled();
  });
});

describe('useConversationSessionEdits updateSessionModel + 回写门禁', () => {
  it('更新 mine 缓存中会话的 model', () => {
    seedMineCache();

    const { result } = renderEdits();
    result.current.updateSessionModel('bot-a:1', 's1', 'claude_code');

    expect(seededSession().model).toBe('claude_code');
  });

  it('管理 Bot 缓存 origin=others 时不回写;好友 Bot 缓存正常回写', () => {
    useConversationStore.setState({
      ...conversationInitialState,
      sessionsByBotId: {
        'bot-a:1': {
          botId: 'bot-a:1',
          origin: 'others',
          scope: 'all',
          sessions: emptyList,
          friendDirectory: { items: [], loading: false, error: null },
          friendGroups: {},
        },
      },
      friendBotSessionsByBotId: {
        'friend:2': { ...emptyList, items: [{ ...baseSession, botId: 'friend:2' }] },
      },
    });

    const { result } = renderEdits();
    result.current.updateSessionModel('bot-a:1', 's1', 'claude_code');
    expect(useConversationStore.getState().sessionsByBotId['bot-a:1']!.origin).toBe('others');
    result.current.updateSessionModel('friend:2', 's1', 'openclaw');
    expect(useConversationStore.getState().friendBotSessionsByBotId['friend:2']!.items[0].model).toBe('openclaw');
  });
});
