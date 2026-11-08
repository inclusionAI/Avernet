/** @jest-environment jsdom */
import type { ConversationBotView } from '@/domain/conversation/types';
import { useConversationDeepLinkSessionHydration } from '@/pages/Workspace/Chat/hooks/useConversationDeepLinkSessionHydration';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { conversationInitialState, useConversationStore } from '@/stores/conversationStore';
import { beforeEach, describe, expect, it } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botSessionService', () => ({
  botSessionService: { getSessionDetail: require('jest-mock').fn() },
}));

const getSessionDetail = botSessionService.getSessionDetail as unknown as jest.Mock;

const managedBot: ChatBotView = {
  botId: 'bot-a:1',
  realBotId: 'bot-a',
  ownerId: '1',
  displayName: '管理 Bot',
  online: true,
  chatable: true,
};
const friendBot: ChatBotView = {
  botId: 'friend:2',
  realBotId: 'friend',
  ownerId: '2',
  displayName: '好友 Bot',
  online: true,
  chatable: true,
  isFriendBot: true,
};
const managedView: ConversationBotView = { bot: managedBot, section: 'managed' };
const friendView: ConversationBotView = { bot: friendBot, section: 'friend' };
const codingBot: ChatBotView = {
  ...managedBot,
  botId: 'coding-bot:1',
  realBotId: 'coding-bot',
  displayName: 'Coding Bot',
  engine: 'claude_code',
  isAgentCodingBot: true,
};
const codingView: ConversationBotView = { bot: codingBot, section: 'team' };
const emptyList = {
  items: [],
  page: 1,
  total: 0,
  hasMore: false,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
};

function sessionOfSessionId(sessionId: string) {
  return {
    sessionId,
    botId: managedBot.botId,
    title: '会话',
    messageCount: 0,
    gmtModified: '',
    gmtCreate: '',
  };
}

function seedManagedStore(sessionIds: string[] = []) {
  useConversationStore.setState({
    ...conversationInitialState,
    selectedBotId: managedBot.botId,
    selectedSection: 'managed',
    selectedOrigin: 'mine',
    selectedScope: 'all',
    selectedSessionId: 's-deep',
    sessionsByBotId: {
      [managedBot.botId]: {
        botId: managedBot.botId,
        origin: 'mine',
        scope: 'all',
        sessions: { ...emptyList, items: sessionIds.map(sessionOfSessionId) },
        friendDirectory: { items: [], loading: false, error: null },
        friendGroups: {},
      },
    },
  });
}

function seedFriendStore() {
  useConversationStore.setState({
    ...conversationInitialState,
    selectedBotId: friendBot.botId,
    selectedSection: 'friend',
    selectedOrigin: 'mine',
    selectedScope: 'all',
    selectedSessionId: 'fs-deep',
    friendBotSessionsByBotId: {
      [friendBot.botId]: emptyList,
    },
  });
}

function renderDeepLinkHydration(
  userId: string,
  managedBots: ConversationBotView[],
  friendBots: ConversationBotView[] = [],
) {
  return renderHook(() =>
    useConversationDeepLinkSessionHydration({
      userId,
      managedBots,
      friendBots,
      hydrated: true,
    }),
  );
}

beforeEach(() => {
  getSessionDetail.mockReset();
  useConversationStore.setState({ ...conversationInitialState });
});

describe('useConversationDeepLinkSessionHydration', () => {
  it('managed/mine 深链且列表 miss 时,按 sessionId 精确拉取会话并写入快照', async () => {
    seedManagedStore();
    getSessionDetail.mockResolvedValue({ ok: true, data: sessionOfSessionId('s-deep') });

    renderDeepLinkHydration('1', [managedView]);

    await waitFor(() => expect(getSessionDetail).toHaveBeenCalledTimes(1));
    expect(getSessionDetail).toHaveBeenCalledWith(managedBot, '1', 's-deep');
    await waitFor(() => {
      const snapshot = useConversationStore.getState().selectedSessionSnapshot;
      expect(snapshot?.section).toBe('managed');
      expect(snapshot?.botId).toBe(managedBot.botId);
      expect(snapshot?.origin).toBe('mine');
      expect(snapshot?.scope).toBe('all');
      expect(snapshot?.session.sessionId).toBe('s-deep');
    });
  });

  it('列表已覆盖目标会话时不重复调用详情接口', async () => {
    seedManagedStore(['s-deep']);

    renderDeepLinkHydration('1', [managedView]);

    await act(async () => {
      await Promise.resolve();
    });

    expect(getSessionDetail).not.toHaveBeenCalled();
  });

  it('friend/mine 深链同样支持详情兜底', async () => {
    seedFriendStore();
    getSessionDetail.mockResolvedValue({
      ok: true,
      data: { ...sessionOfSessionId('fs-deep'), botId: friendBot.botId },
    });

    renderDeepLinkHydration('2', [], [friendView]);

    await waitFor(() => expect(getSessionDetail).toHaveBeenCalledTimes(1));
    expect(getSessionDetail).toHaveBeenCalledWith(friendBot, '2', 'fs-deep');
    await waitFor(() => {
      expect(useConversationStore.getState().selectedSessionSnapshot?.session.sessionId).toBe('fs-deep');
    });
  });

  it('Coding Bot others 深链允许精确拉取详情并保留创建者身份', async () => {
    const sessionId = 'user:200:session:shared:agent:coding-bot';
    useConversationStore.setState({
      ...conversationInitialState,
      selectedBotId: codingBot.botId,
      selectedSection: 'team',
      selectedOrigin: 'others',
      selectedScope: 'all',
      selectedFriendUserId: '200',
      selectedSessionId: sessionId,
      sessionsByBotId: {
        [codingBot.botId]: {
          botId: codingBot.botId,
          origin: 'others',
          scope: 'all',
          sessions: emptyList,
          friendDirectory: { items: [], loading: false, error: null },
          friendGroups: {},
        },
      },
    });
    getSessionDetail.mockResolvedValue({
      ok: true,
      data: { ...sessionOfSessionId(sessionId), botId: codingBot.botId },
    });

    renderDeepLinkHydration('100', [codingView]);

    await waitFor(() => expect(getSessionDetail).toHaveBeenCalledWith(codingBot, '100', sessionId));
    await waitFor(() => {
      expect(useConversationStore.getState().selectedSessionSnapshot).toMatchObject({
        section: 'team',
        botId: codingBot.botId,
        origin: 'others',
        friendUserId: '200',
        session: { sessionId },
      });
    });
  });

  it('普通 Bot others 仍由只读好友链路处理，不调用会话详情接口', async () => {
    useConversationStore.setState({
      ...conversationInitialState,
      selectedBotId: managedBot.botId,
      selectedSection: 'managed',
      selectedOrigin: 'others',
      selectedScope: 'all',
      selectedFriendUserId: '200',
      selectedSessionId: 'shared-session',
    });

    renderDeepLinkHydration('100', [managedView]);
    await act(async () => Promise.resolve());

    expect(getSessionDetail).not.toHaveBeenCalled();
  });

  it('Coding Bot 详情归属与当前选择不一致时不写入快照', async () => {
    const sessionId = 'user:100:session:mine:agent:coding-bot';
    useConversationStore.setState({
      ...conversationInitialState,
      selectedBotId: codingBot.botId,
      selectedSection: 'team',
      selectedOrigin: 'others',
      selectedScope: 'all',
      selectedFriendUserId: '200',
      selectedSessionId: sessionId,
    });
    getSessionDetail.mockResolvedValue({
      ok: true,
      data: { ...sessionOfSessionId(sessionId), botId: codingBot.botId },
    });

    renderDeepLinkHydration('100', [codingView]);
    await act(async () => Promise.resolve());

    expect(getSessionDetail).not.toHaveBeenCalled();
    expect(useConversationStore.getState().selectedSessionSnapshot).toBeNull();
  });

  it('请求返回前选中会话已切换时,丢弃旧响应', async () => {
    seedManagedStore();
    let resolveDetail!: (value: { ok: true; data: ReturnType<typeof sessionOfSessionId> }) => void;
    getSessionDetail.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveDetail = resolve;
        }) as ReturnType<typeof botSessionService.getSessionDetail>,
    );
    getSessionDetail.mockImplementation(
      () => new Promise(() => {}) as ReturnType<typeof botSessionService.getSessionDetail>,
    );

    renderDeepLinkHydration('1', [managedView]);

    await waitFor(() => expect(getSessionDetail).toHaveBeenCalledTimes(1));
    act(() => {
      useConversationStore.getState().selectConversation({
        botId: managedBot.botId,
        section: 'managed',
        origin: 'mine',
        scope: 'all',
        friendUserId: null,
        sessionId: 's-other',
      });
    });

    await act(async () => {
      resolveDetail({ ok: true, data: sessionOfSessionId('s-deep') });
      await Promise.resolve();
    });

    expect(useConversationStore.getState().selectedSessionSnapshot).toBeNull();
  });
});
