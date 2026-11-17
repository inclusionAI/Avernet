/** @jest-environment jsdom */
import { extendCapabilities, type ConversationInstanceScopeState } from '@/capabilities';
import type { ConversationBotView } from '@/domain/conversation/types';
import { resolveSessionFromContainer, useConversationDeepLinkSessionHydration } from '@/pages/Workspace/Chat/hooks/useConversationDeepLinkSessionHydration';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { botContainerService } from '@/services/botWorkshop/botContainerService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { conversationInitialState, useConversationStore } from '@/stores/conversationStore';
import { beforeEach, describe, expect, it } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botSessionService', () => {
  const actual = jest.requireActual('@/services/workspace/botSessionService');
  return { ...actual, botSessionService: { getSessionDetail: require('jest-mock').fn() } };
});
jest.mock('@/services/botWorkshop/botContainerService', () => ({
  botContainerService: { list: require('jest-mock').fn() },
}));

const getSessionDetail = botSessionService.getSessionDetail as unknown as jest.Mock;
const listBotContainers = botContainerService.list as unknown as jest.Mock;

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

const serviceBot: ChatBotView = {
  ...managedBot,
  botId: 'service-bot:1',
  realBotId: 'service-bot',
  ownerId: '1',
  displayName: '多实例 Service Bot',
  botType: 'service',
  engine: 'openclaw',
  runtimeStage: 'online',
};
const serviceView: ConversationBotView = {
  bot: serviceBot,
  section: 'managed',
  origin: 'mine',
} as unknown as ConversationBotView;
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

let instanceState: ConversationInstanceScopeState;
let instanceListener: (() => void) | undefined;

beforeEach(() => {
  instanceState = { service: true, multiInstance: true, ready: true };
  instanceListener = undefined;
  extendCapabilities({ getConversationInstanceScope: () => ({ status: 'available', value: {
    InstancePicker: () => null,
    getState: () => instanceState,
    subscribe: (_botId, listener) => { instanceListener = listener; return () => { instanceListener = undefined; }; },
    ensureInstancesLoaded: () => {},
    getSessionRange: () => null,
  } }) });
  useErrorNotifyStore.getState().reset();
  getSessionDetail.mockReset();
  listBotContainers.mockReset();
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
  it('多实例 Service Bot 深链会话会按实例逐个探测并选中正确容器', async () => {
    useConversationStore.setState({
      ...conversationInitialState,
      selectedBotId: 'service-bot:1',
      selectedSection: 'managed',
      selectedOrigin: 'mine',
      selectedScope: 'all',
      selectedSessionId: 's-service',
    });
    listBotContainers.mockResolvedValue({
      botId: serviceBot.botId,
      instances: [{ id: 'device-a' }, { id: 'device-b' }],
    });
    getSessionDetail.mockImplementation((bot, userId, sessionId, range) => {
      if (range?.deviceUuid === 'device-b') {
        return {
          ok: true,
          data: { ...sessionOfSessionId('s-service'), botId: serviceBot.botId },
        };
      }
      return { ok: false, error: { code: 'NOT_FOUND', friendlyMessage: 'not found', canRetry: true } };
    });

    renderDeepLinkHydration('1', [serviceView]);
    await waitFor(() => expect(getSessionDetail).toHaveBeenCalledTimes(2));

    expect(getSessionDetail).toHaveBeenNthCalledWith(
      1,
      serviceBot,
      '1',
      's-service',
      { deviceUuid: 'device-a', stage: 'online' },
      { silentNotFound: true },
    );
    expect(getSessionDetail).toHaveBeenNthCalledWith(
      2,
      serviceBot,
      '1',
      's-service',
      { deviceUuid: 'device-b', stage: 'online' },
      { silentNotFound: true },
    );

    const state = useConversationStore.getState();
    expect(state.instanceSelectionByBotId[serviceBot.botId]).toBe('device-b');
    expect(state.selectedSessionSnapshot?.session.sessionId).toBe('s-service');
  });
});


it('reports one result after all instances miss', async () => {
  useConversationStore.setState({ ...conversationInitialState,
    selectedBotId: serviceBot.botId, selectedSection: 'managed', selectedOrigin: 'mine',
    selectedScope: 'all', selectedSessionId: 'missing',
  });
  listBotContainers.mockResolvedValue({ instances: [{ id: 'a' }, { id: 'b' }] });
  getSessionDetail.mockResolvedValue({ ok: false, error: { code: 'NOT_FOUND', friendlyMessage: 'not found', canRetry: true } });
  renderDeepLinkHydration('1', [serviceView]);
  await waitFor(() => expect(useErrorNotifyStore.getState().queue).toHaveLength(1));
  expect(getSessionDetail).toHaveBeenCalledTimes(2);
  expect(useErrorNotifyStore.getState().queue[0].message).toContain('未找到目标会话');
});

it('does not continue probing on non-missing failures', async () => {
  listBotContainers.mockResolvedValue({ instances: [{ id: 'a' }, { id: 'b' }] });
  const result = { ok: false, error: { code: 'BOT_SESSION_FAILED', friendlyMessage: 'forbidden', canRetry: true } };
  getSessionDetail.mockResolvedValue(result);
  expect(await resolveSessionFromContainer(serviceBot, '1', 's')).toEqual({ result });
  expect(getSessionDetail).toHaveBeenCalledTimes(1);
});


it('does not notify for a lookup that finishes after switching sessions', async () => {
  useConversationStore.setState({ ...conversationInitialState,
    selectedBotId: serviceBot.botId, selectedSection: 'managed', selectedOrigin: 'mine',
    selectedScope: 'all', selectedSessionId: 'old',
  });
  let finish!: (value: unknown) => void;
  listBotContainers.mockResolvedValue({ instances: [{ id: 'a' }] });
  getSessionDetail.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const { unmount } = renderDeepLinkHydration('1', [serviceView]);
  await waitFor(() => expect(getSessionDetail).toHaveBeenCalledTimes(1));
  act(() => useConversationStore.setState({ selectedSessionId: null }));
  await act(async () => {
    finish({ ok: false, error: { code: 'NOT_FOUND', friendlyMessage: 'not found', canRetry: true } });
  });
  expect(useErrorNotifyStore.getState().queue).toHaveLength(0);
  unmount();
});


it.each([undefined, 'old-device'])('single-instance service bots use direct requests (selection=%s)', async (selection) => {
  instanceState = { service: true, multiInstance: false, ready: true };
  const result = { ok: true, data: { ...sessionOfSessionId('s'), botId: serviceBot.botId } };
  getSessionDetail.mockResolvedValue(result);
  expect(await resolveSessionFromContainer(serviceBot, '1', 's', selection)).toEqual({ result });
  expect(getSessionDetail).toHaveBeenCalledTimes(1);
  expect(getSessionDetail).toHaveBeenCalledWith(serviceBot, '1', 's');
  expect(listBotContainers).not.toHaveBeenCalled();
});

it('does not probe instances when the host has no instance capability', async () => {
  extendCapabilities({ getConversationInstanceScope: () => ({ status: 'unsupported', value: null }) });
  getSessionDetail.mockResolvedValue({ ok: false, error: { code: 'BOT_SESSION_FAILED' } });
  await resolveSessionFromContainer(serviceBot, '1', 's');
  expect(getSessionDetail).toHaveBeenCalledWith(serviceBot, '1', 's');
  expect(listBotContainers).not.toHaveBeenCalled();
});

it('waits for existing instance discovery and then directly queries a single-instance service bot', async () => {
  instanceState = { service: true, multiInstance: false, ready: false };
  useConversationStore.setState({ ...conversationInitialState,
    selectedBotId: serviceBot.botId, selectedSection: 'managed', selectedOrigin: 'mine',
    selectedScope: 'all', selectedSessionId: 's',
  });
  getSessionDetail.mockResolvedValue({ ok: true, data: { ...sessionOfSessionId('s'), botId: serviceBot.botId } });
  renderDeepLinkHydration('1', [serviceView]);
  expect(getSessionDetail).not.toHaveBeenCalled();
  expect(listBotContainers).not.toHaveBeenCalled();
  act(() => {
    instanceState = { service: true, multiInstance: false, ready: true };
    instanceListener?.();
  });
  await waitFor(() => expect(getSessionDetail).toHaveBeenCalledTimes(1));
  expect(getSessionDetail).toHaveBeenCalledWith(serviceBot, '1', 's');
  expect(listBotContainers).not.toHaveBeenCalled();
});


it.each([
  { ...serviceBot, botType: 'personal' },
  { ...serviceBot, isFriendBot: true },
  { ...serviceBot, engine: 'teclaw' },
  { ...serviceBot, isAgentCodingBot: true },
])('keeps ordinary bot lookup unchanged: %j', async (bot) => {
  getSessionDetail.mockResolvedValue({ ok: false, error: { code: 'BOT_SESSION_FAILED' } });
  await resolveSessionFromContainer(bot, '1', 's', 'device-a');
  expect(getSessionDetail).toHaveBeenCalledTimes(1);
  expect(getSessionDetail).toHaveBeenCalledWith(bot, '1', 's');
  expect(listBotContainers).not.toHaveBeenCalled();
});
