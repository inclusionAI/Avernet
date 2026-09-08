/** @jest-environment jsdom */
import { useBotSessions } from '@/pages/Workspace/hooks/useBotSessions';
import { botSessionService, type BotChatSessionView, type ChatBotView } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botSessionService');
const svc = botSessionService as unknown as {
  listSessions: jest.Mock<any>;
  listSessionsPage: jest.Mock<any>;
  listFavoriteSessionsPage: jest.Mock<any>;
  createSession: jest.Mock<any>;
  deleteSession: jest.Mock<any>;
  getSessionDetail: jest.Mock<any>;
};

const bot: ChatBotView = { botId: 'b:1', realBotId: 'b', ownerId: '1', displayName: 'B', online: true, chatable: true };
const s1: BotChatSessionView = {
  sessionId: 's1',
  botId: 'b:1',
  title: '会话1',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
};
const s2: BotChatSessionView = {
  sessionId: 's2',
  botId: 'b:1',
  title: '会话2',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
};

beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().resetWorkspace();
  svc.listSessions.mockResolvedValue({ ok: true, data: [s2, s1] });
  svc.listSessionsPage.mockResolvedValue({ ok: true, data: { items: [s2, s1], total: 2 } });
  svc.listFavoriteSessionsPage.mockResolvedValue({ ok: true, data: { items: [], total: 0 } });
  svc.createSession.mockResolvedValue({
    ok: true,
    data: { sessionId: 's3', botId: 'b:1', title: '新', messageCount: 0, gmtModified: '', gmtCreate: '' },
  });
  svc.deleteSession.mockResolvedValue({ ok: true, data: null });
  svc.getSessionDetail.mockResolvedValue({ ok: false });
});

it('展开 bot 时懒加载会话并缓存', async () => {
  const { result } = renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  await waitFor(() => expect(svc.listSessionsPage).toHaveBeenCalledWith(bot, 'human-1', 1, 10));
  await waitFor(() => expect(result.current.sessionsByBotId['b:1']).toHaveLength(2));
});

it('展开 bot 并加载会话后自动选中首条', async () => {
  const { result } = renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));

  await waitFor(() => expect(result.current.sessionsByBotId['b:1']).toHaveLength(2));
  await waitFor(() => expect(result.current.selectedBotSessionId).toBe('s2'));
  expect(result.current.selectedSession?.sessionId).toBe('s2');
});

it('createSession 选中新建会话并前置', async () => {
  const { result } = renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  await waitFor(() => expect(result.current.sessionsByBotId['b:1']).toBeDefined());
  await act(async () => {
    await result.current.createSession(bot);
  });
  await waitFor(() => expect(result.current.selectedBotSessionId).toBe('s3'));
});

it('deleteSession 后从列表移除', async () => {
  const { result } = renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  await waitFor(() => expect(result.current.sessionsByBotId['b:1']).toHaveLength(2));
  await act(async () => {
    await result.current.deleteSession(bot, 's1');
  });
  await waitFor(() => expect(result.current.sessionsByBotId['b:1'].find((s) => s.sessionId === 's1')).toBeUndefined());
});

function makeSessions(prefix: string, count: number): BotChatSessionView[] {
  return Array.from({ length: count }, (_, index) => ({
    sessionId: `${prefix}-${index}`,
    botId: 'b:1',
    title: `会话${index}`,
    messageCount: index,
    gmtModified: '',
    gmtCreate: '',
  }));
}

it('普通会话按 10 条加载更多并按 sessionId 去重', async () => {
  const firstPage = makeSessions('all', 10);
  const secondPage = [firstPage[9], { ...firstPage[0], sessionId: 'all-10' }];
  svc.listSessionsPage.mockReset();
  svc.listSessionsPage
    .mockResolvedValueOnce({ ok: true, data: { items: firstPage, total: 11 } })
    .mockResolvedValueOnce({ ok: true, data: { items: secondPage, total: 11 } });

  const { result } = renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  await waitFor(() => expect(result.current.sessionPageMetaByBotId['b:1']?.hasMore).toBe(true));
  await act(async () => {
    await result.current.loadMoreSessions('b:1', 'all');
  });

  expect(svc.listSessionsPage).toHaveBeenNthCalledWith(2, bot, 'human-1', 2, 10);
  expect(result.current.sessionsByBotId['b:1']).toHaveLength(11);
  expect(result.current.sessionPageMetaByBotId['b:1']).toMatchObject({ total: 11, hasMore: false, nextPage: 3 });
});

it('重复点击加载更多只发起一次请求', async () => {
  const firstPage = makeSessions('all', 10);
  let resolveNext: (value: any) => void = () => {};
  const nextPage = new Promise((resolve) => {
    resolveNext = resolve;
  });
  svc.listSessionsPage.mockReset();
  svc.listSessionsPage
    .mockResolvedValueOnce({ ok: true, data: { items: firstPage, total: 20 } })
    .mockReturnValueOnce(nextPage);

  const { result } = renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  await waitFor(() => expect(result.current.sessionPageMetaByBotId['b:1']?.hasMore).toBe(true));
  let firstLoad: Promise<void>;
  let secondLoad: Promise<void>;
  await act(async () => {
    firstLoad = result.current.loadMoreSessions('b:1', 'all');
    secondLoad = result.current.loadMoreSessions('b:1', 'all');
  });
  expect(svc.listSessionsPage).toHaveBeenCalledTimes(2);
  resolveNext({ ok: true, data: { items: [], total: 20 } });
  await act(async () => {
    await Promise.all([firstLoad!, secondLoad!]);
  });
});

it('收藏会话独立分页，加载失败后允许重试', async () => {
  const firstPage = makeSessions('favorite', 10).map((session) => ({ ...session, favorite: true }));
  const secondPage = [firstPage[9], { ...firstPage[0], sessionId: 'favorite-10' }];
  svc.listFavoriteSessionsPage.mockReset();
  svc.listFavoriteSessionsPage
    .mockResolvedValueOnce({ ok: false, error: { friendlyMessage: '收藏加载失败' } })
    .mockResolvedValueOnce({ ok: true, data: { items: firstPage, total: 11 } })
    .mockResolvedValueOnce({ ok: true, data: { items: secondPage, total: 11 } });

  const { result } = renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  await act(async () => {
    await result.current.loadFavoriteSessions('b:1');
  });
  await act(async () => {
    await result.current.loadFavoriteSessions('b:1');
  });
  await waitFor(() => expect(result.current.favoriteSessionPageMetaByBotId['b:1']?.hasMore).toBe(true));
  await act(async () => {
    await result.current.loadMoreSessions('b:1', 'favorite');
  });

  expect(svc.listFavoriteSessionsPage).toHaveBeenCalledTimes(3);
  expect(svc.listFavoriteSessionsPage).toHaveBeenNthCalledWith(2, bot, 'human-1', 1, 10);
  expect(svc.listFavoriteSessionsPage).toHaveBeenNthCalledWith(3, bot, 'human-1', 2, 10);
  expect(result.current.favoriteSessionsByBotId['b:1']).toHaveLength(11);
});

it('身份切换后旧请求结果不会回填新身份', async () => {
  let resolveFirst: (value: any) => void = () => {};
  let resolveSecond: (value: any) => void = () => {};
  svc.listSessionsPage.mockReset();
  svc.listSessionsPage
    .mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFirst = resolve;
      }),
    )
    .mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSecond = resolve;
      }),
    );

  const { result, rerender } = renderHook(({ identityId }) => useBotSessions([bot], ['b:1'], identityId), {
    initialProps: { identityId: 'human-1' },
  });
  await waitFor(() => expect(svc.listSessionsPage).toHaveBeenCalledTimes(1));
  rerender({ identityId: 'human-2' });
  await waitFor(() => expect(svc.listSessionsPage).toHaveBeenCalledTimes(2));
  resolveFirst({ ok: true, data: { items: [s1], total: 1 } });
  resolveSecond({ ok: true, data: { items: [s2], total: 1 } });

  await waitFor(() => expect(result.current.sessionsByBotId['b:1']?.[0]?.sessionId).toBe('s2'));
});

it('不同 Bot 存在相同 sessionId 时选中点击的 Bot 会话', async () => {
  const botA: ChatBotView = {
    botId: 'bot-a:1',
    realBotId: 'bot-a',
    ownerId: '1',
    displayName: 'Bot A',
    online: true,
    chatable: true,
  };
  const botB: ChatBotView = {
    botId: 'bot-b:1',
    realBotId: 'bot-b',
    ownerId: '1',
    displayName: 'Bot B',
    online: true,
    chatable: true,
  };
  const sessionA: BotChatSessionView = {
    sessionId: 'same-session',
    botId: botA.botId,
    title: 'Bot A 会话',
    messageCount: 1,
    gmtModified: '',
    gmtCreate: '',
  };
  const sessionB: BotChatSessionView = {
    sessionId: 'same-session',
    botId: botB.botId,
    title: 'Bot B 会话',
    messageCount: 2,
    gmtModified: '',
    gmtCreate: '',
  };
  svc.listSessionsPage.mockReset();
  svc.listSessionsPage
    .mockResolvedValueOnce({ ok: true, data: { items: [sessionA], total: 1 } })
    .mockResolvedValueOnce({ ok: true, data: { items: [sessionB], total: 1 } });

  const { result, rerender } = renderHook(
    ({ expandedBotId }) => useBotSessions([botA, botB], [expandedBotId], 'human-1'),
    { initialProps: { expandedBotId: botA.botId } },
  );
  await waitFor(() => expect(result.current.sessionsByBotId[botA.botId]).toHaveLength(1));
  rerender({ expandedBotId: botB.botId });
  await waitFor(() => expect(result.current.sessionsByBotId[botB.botId]).toHaveLength(1));

  await act(async () => {
    result.current.openSession(botB.botId, sessionB.sessionId);
  });

  expect(result.current.selectedSession?.botId).toBe(botB.botId);
  expect(result.current.selectedSession?.title).toBe(sessionB.title);
});

it('选中首页外会话时直接拉取详情并补入列表（外链直达旧会话）', async () => {
  const oldSession: BotChatSessionView = {
    sessionId: 's-old',
    botId: 'b:1',
    title: '旧会话',
    messageCount: 5,
    gmtModified: '',
    gmtCreate: '',
  };
  svc.getSessionDetail.mockResolvedValue({ ok: true, data: oldSession });

  const { result } = renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  // 首页加载（仅 s1、s2），不含 s-old
  await waitFor(() => expect(result.current.sessionsByBotId['b:1']).toHaveLength(2));

  // 选中首页外的旧会话
  await act(async () => {
    result.current.selectSession('s-old');
  });

  // 兜底 effect 直接拉取该会话详情并补入列表
  await waitFor(() => expect(svc.getSessionDetail).toHaveBeenCalledWith(bot, 'human-1', 's-old'));
  await waitFor(() => expect(result.current.sessionsByBotId['b:1'].find((s) => s.sessionId === 's-old')).toBeDefined());
  await waitFor(() => expect(result.current.selectedSession?.sessionId).toBe('s-old'));
});

it('记忆的 bot 会话已删除：详情反查失败后回落首条', async () => {
  useWorkspaceStore.getState().setView('chat');
  useWorkspaceStore.getState().selectBotSession('gone');
  renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  await waitFor(() => expect(svc.getSessionDetail).toHaveBeenCalledWith(bot, 'human-1', 'gone'));
  await waitFor(() => expect(useWorkspaceStore.getState().selectedBotSessionId).toBe('s2'));
});

it('展开的 bot 已不在可用列表：清空悬空选中', async () => {
  useWorkspaceStore.getState().setView('chat');
  useWorkspaceStore.getState().selectBotSession('sX');
  renderHook(() => useBotSessions([bot], ['ghost-bot'], 'human-1'));
  await waitFor(() => expect(useWorkspaceStore.getState().selectedBotSessionId).toBeNull());
});

it('bot 列表仍在加载（好友 bot 未到位）：不清空展开 bot 缺失时的选中', async () => {
  useWorkspaceStore.getState().setView('chat');
  useWorkspaceStore.getState().selectBotSession('sF1');
  const { rerender } = renderHook(
    ({ loading }: { loading: boolean }) => useBotSessions([bot], ['fr:1'], 'human-1', loading),
    { initialProps: { loading: true } },
  );
  await act(async () => Promise.resolve());
  // 好友 bot 列表在途：选中必须保住（旧实现 chatBots.length>0 即清空）。
  expect(useWorkspaceStore.getState().selectedBotSessionId).toBe('sF1');

  // 列表加载完成且 fr:1 确实不存在 → 兜底恢复，清空悬空选中。
  rerender({ loading: false });
  await waitFor(() => expect(useWorkspaceStore.getState().selectedBotSessionId).toBeNull());
});

it('兜底反查在途时用户已切换选中：失败回调不得劫持新选中', async () => {
  const detailResolvers: Array<(value: { ok: false }) => void> = [];
  svc.getSessionDetail.mockImplementation(
    () =>
      new Promise((resolve) => {
        detailResolvers.push(resolve);
      }),
  );
  useWorkspaceStore.getState().setView('chat');
  useWorkspaceStore.getState().selectBotSession('gone');
  renderHook(() => useBotSessions([bot], ['b:1'], 'human-1'));
  await waitFor(() => expect(svc.getSessionDetail).toHaveBeenCalledWith(bot, 'human-1', 'gone'));

  // 反查在途期间用户点选了 s1。
  act(() => {
    useWorkspaceStore.getState().selectBotSession('s1');
  });
  await act(async () => {
    detailResolvers.splice(0).forEach((resolve) => resolve({ ok: false }));
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(useWorkspaceStore.getState().selectedBotSessionId).toBe('s1');
});

const friendBot: ChatBotView = {
  botId: 'botfriend:999',
  realBotId: 'botfriend',
  ownerId: '999',
  displayName: '好友Bot',
  online: true,
  chatable: true,
  isFriendBot: true,
};

it('展开「好友 Bot」分类下的 bot：按 isFriendBot 调用会话列表接口（服务层据此附带 f_user_id）', async () => {
  useWorkspaceStore.getState().setView('chat');
  const { result } = renderHook(() => useBotSessions([friendBot], ['botfriend:999'], 'human_327325', false));
  // 懒加载必须以好友 bot 实体（isFriendBot=true、realBotId 拆分）与当前用户 id 调用 listSessionsPage；
  // 服务层 withFriendBotRequestParams 据此在 GET /openapi/v1/bots/{realBotId}/sessions 上附带 f_user_id。
  await waitFor(() => expect(svc.listSessionsPage).toHaveBeenCalledWith(friendBot, 'human_327325', 1, 10));
  const [calledBot] = svc.listSessionsPage.mock.calls[0];
  expect(calledBot.isFriendBot).toBe(true);
  expect(calledBot.realBotId).toBe('botfriend');
  expect(result.current.sessionsByBotId['botfriend:999']).toBeDefined();
});

it('openSession 好友 bot：展开时分区归属记为 friend（缺省 mine 会让侧栏好友分区折叠）', async () => {
  useWorkspaceStore.getState().setView('chat');
  const { result } = renderHook(() => useBotSessions([friendBot], [], 'human_327325', false));
  act(() => {
    result.current.openSession('botfriend:999', 's1');
  });
  expect(useWorkspaceStore.getState().expandedBotIds['botfriend:999']).toBe(true);
  expect(useWorkspaceStore.getState().expandedBotSectionKey['botfriend:999']).toBe('friend');
});

it('收起后重新展开 bot：每次切换 bot 都重新拉取最新会话列表', async () => {
  const { rerender } = renderHook(
    ({ expanded }: { expanded: string[] }) => useBotSessions([bot], expanded, 'human-1'),
    {
      initialProps: { expanded: [] as string[] },
    },
  );
  await act(async () => Promise.resolve());
  expect(svc.listSessionsPage).not.toHaveBeenCalled();

  rerender({ expanded: ['b:1'] });
  await waitFor(() => expect(svc.listSessionsPage).toHaveBeenCalledTimes(1));

  // 收起再展开（= 切换到别的 bot 再切回来）：必须重新请求获取最新列表。
  rerender({ expanded: [] });
  await act(async () => Promise.resolve());
  rerender({ expanded: ['b:1'] });
  await waitFor(() => expect(svc.listSessionsPage).toHaveBeenCalledTimes(2));
});
