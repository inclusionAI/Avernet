/** @jest-environment jsdom */
import { useBotChats } from '@/hooks/useBotChats';
import { botChatService } from '@/services/botWorkshop/botChatService';
import { identityService } from '@/services/workspace';
import { useBotChatStore } from '@/stores/botChatStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook, waitFor } from '@testing-library/react';
import { history, useLocation } from '@umijs/max';

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  useLocation: jest.fn(),
}));
jest.mock('@/services/botWorkshop/botChatService', () => ({
  botChatService: {
    list: jest.fn(),
    detail: jest.fn(),
    related: jest.fn(),
  },
}));
jest.mock('@/services/workspace', () => ({
  identityService: { loadIdentities: jest.fn() },
  isTestUserIdentity: jest.fn(() => false),
  resolveUserId: jest.fn((value: string) => value),
}));

const mockedUseLocation = useLocation as jest.MockedFunction<typeof useLocation>;
const mockedList = botChatService.list as jest.MockedFunction<typeof botChatService.list>;
const mockedDetail = botChatService.detail as jest.MockedFunction<typeof botChatService.detail>;
const mockedRelated = botChatService.related as jest.MockedFunction<typeof botChatService.related>;
const mockedLoadIdentities = identityService.loadIdentities as jest.MockedFunction<
  typeof identityService.loadIdentities
>;

// useBotChats 的 userId 恒取当前登录用户（防越权，安全修复 PR 258）：优先取 workspaceStore
// 已种入的登录身份；store 无真实人类身份时走 identityService.loadIdentities 兜底加载，故同时
// mock 一个真实人类身份。ActingUserId 刻意不等于任何用例 URL 里的 user_id，证明 URL 参数被忽略。
const ActingUserId = 'user-login';

// PR258 后 user_id 恒取登录身份（不再读 URL 参数）：用例在 workspaceStore 种入登录身份（≠ URL 的 user_id）。
const seedLoginIdentity = (id: string) => {
  useWorkspaceStore.getState().setIdentities([{ id, kind: 'user', displayName: id, online: true }], id);
};

beforeEach(() => {
  jest.clearAllMocks();
  useBotChatStore.getState().reset();
  useWorkspaceStore.getState().setIdentities([], null);
  mockedList.mockResolvedValue({ items: [], total: 0, page: 1, limit: 20, hasMore: false });
  mockedLoadIdentities.mockResolvedValue({
    ok: true,
    data: {
      identities: [{ id: ActingUserId, kind: 'user', displayName: '测试用户', online: true }],
      defaultActiveId: ActingUserId,
    },
  });
});

it('从独立页面参数初始化日志上下文并查询真实 Service', async () => {
  mockedUseLocation.mockReturnValue({
    pathname: '/bot-workshop/logs',
    search: '?bot_id=default&user_id=user-demo&bot_name=My%20Bot',
    hash: '',
    state: null,
    key: 'logs',
  });
  seedLoginIdentity(ActingUserId);

  const { unmount } = renderHook(() => useBotChats());

  await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));
  // URL 携带 user_id=user-demo 但被忽略：userId 恒取当前登录用户。
  expect(mockedList).toHaveBeenCalledWith(
    { botId: 'default', botName: 'My Bot', ownerId: undefined, userId: ActingUserId },
    expect.objectContaining({ traceId: '', sessionKey: '' }),
  );
  expect(useBotChatStore.getState().context).toEqual({
    botId: 'default',
    botName: 'My Bot',
    ownerId: undefined,
    userId: ActingUserId,
  });

  unmount();
  expect(useBotChatStore.getState().context).toBeUndefined();
});

it('缺少 bot_id 时不查询并可返回 Bot 工坊', async () => {
  mockedUseLocation.mockReturnValue({
    pathname: '/bot-workshop/logs',
    search: '?user_id=user-demo',
    hash: '',
    state: null,
    key: 'logs-missing-bot',
  });

  const { result } = renderHook(() => useBotChats());

  expect(result.current.initializationError).toContain('缺少 bot_id');
  expect(mockedList).not.toHaveBeenCalled();
  act(() => result.current.backToWorkshop()); // 内部 reset store 触发重渲,包 act 消除告警。
  expect(history.push).toHaveBeenCalledWith('/bot-workshop');
});

it('群模式打开其他 Bot Trace 时保留 Group 锚点且不重查关联列表', async () => {
  mockedUseLocation.mockReturnValue({
    pathname: '/bot-workshop/logs',
    search: '?bot_id=viewer-bot&user_id=collaborator&owner_id=owner',
    hash: '',
    state: null,
    key: 'logs-group',
  });
  mockedDetail.mockResolvedValue({
    id: 'other-trace',
    timestamp: '2026-08-24T00:00:00Z',
    name: 'Other Trace',
    groupId: 'group-1',
    status: 'SUCCESS',
    latencyMs: 0,
    totalTokens: 0,
    totalCost: 0,
    observations: [],
  });
  seedLoginIdentity(ActingUserId);

  const { result } = renderHook(() => useBotChats());
  await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));
  useBotChatStore.getState().setDetailState({
    detail: {
      id: 'current-trace',
      timestamp: '2026-08-24T00:00:00Z',
      name: 'Current Trace',
      groupId: 'group-1',
      status: 'SUCCESS',
      latencyMs: 0,
      totalTokens: 0,
      totalCost: 0,
      observations: [],
    },
  });
  useBotChatStore.getState().setRelatedState({
    relationScope: 'group',
    related: { items: [], total: 2, page: 1, limit: 100, hasMore: false },
  });

  await result.current.openDetail('other-trace');

  expect(mockedDetail).toHaveBeenCalledWith(
    { botId: 'viewer-bot', botName: 'viewer-bot', userId: ActingUserId, ownerId: 'owner' },
    'other-trace',
    'group-1',
  );
  expect(mockedRelated).not.toHaveBeenCalled();
});

it('从 session 关联列表打开 Trace 时直接请求详情，不重复查询 session 历史', async () => {
  mockedUseLocation.mockReturnValue({
    pathname: '/bot-workshop/logs',
    search: '?bot_id=viewer-bot&user_id=owner',
    hash: '',
    state: null,
    key: 'logs-session-related',
  });
  const detail = {
    id: 'related-trace',
    timestamp: '2026-08-24T00:00:00Z',
    name: 'Related Trace',
    sessionId: 'session-id-1',
    sessionKey: 'session-key-1',
    status: 'SUCCESS',
    latencyMs: 0,
    totalTokens: 0,
    totalCost: 0,
    observations: [],
  };
  mockedDetail.mockResolvedValue(detail);
  seedLoginIdentity(ActingUserId);

  const { result } = renderHook(() => useBotChats());
  await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));
  useBotChatStore.getState().setDetailState({
    detail: { ...detail, id: 'current-trace' },
  });
  useBotChatStore.getState().setRelatedState({
    relationScope: 'session',
    related: {
      items: [
        {
          ...detail,
          id: 'related-trace',
          botId: 'viewer-bot',
        },
      ],
      total: 1,
      page: 1,
      limit: 100,
      hasMore: false,
    },
  });

  await result.current.openDetail('related-trace');

  expect(mockedDetail).toHaveBeenCalledWith(
    { botId: 'viewer-bot', botName: 'viewer-bot', userId: ActingUserId, ownerId: undefined },
    'related-trace',
    undefined,
    'viewer-bot',
    undefined,
    true,
  );
  expect(mockedRelated).not.toHaveBeenCalled();
});

it('关联列表加载更多时请求下一页并使用追加模式', async () => {
  mockedUseLocation.mockReturnValue({
    pathname: '/bot-workshop/logs',
    search: '?bot_id=viewer-bot&user_id=owner',
    hash: '',
    state: null,
    key: 'logs-load-more',
  });
  mockedRelated.mockResolvedValue({ items: [], total: 2, page: 2, limit: 100, hasMore: false });
  seedLoginIdentity(ActingUserId);

  const { result } = renderHook(() => useBotChats());
  await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));
  const detail = {
    id: 'current-trace',
    timestamp: '2026-08-24T00:00:00Z',
    name: 'Current Trace',
    groupId: 'group-1',
    status: 'SUCCESS',
    latencyMs: 0,
    totalTokens: 0,
    totalCost: 0,
    observations: [],
  };
  useBotChatStore.getState().setDetailState({ detail });
  useBotChatStore.getState().setRelatedState({
    relationScope: 'group',
    related: { items: [], total: 2, page: 1, limit: 100, hasMore: true },
  });

  await result.current.loadMoreRelated();

  expect(mockedRelated).toHaveBeenCalledWith(
    { botId: 'viewer-bot', botName: 'viewer-bot', userId: ActingUserId, ownerId: undefined },
    detail,
    'group',
    2,
    true,
  );
});
