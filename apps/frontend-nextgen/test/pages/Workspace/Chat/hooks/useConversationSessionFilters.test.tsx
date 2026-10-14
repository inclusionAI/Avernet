/** @jest-environment jsdom */
// 会话列表过滤(sessionKey/好友范围)生命周期回归:真实 Store 驱动 Hook,mock Service 延迟结果。
import type { ConversationBotView } from '@/domain/conversation';
import { firstPageOf } from '@/pages/Workspace/Chat/hooks/conversationSessionCache';
import { useConversationSessions } from '@/pages/Workspace/Chat/hooks/useConversationSessions';
import { botSessionService } from '@/services/workspace/botSessionService';
import { conversationFavoriteService } from '@/services/workspace/conversationFavoriteService';
import { conversationService } from '@/services/workspace/conversationService';
import { useConversationStore } from '@/stores/conversationStore';
import { act, renderHook, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@/services/workspace/conversationService', () => ({
  conversationService: { listManagedSessions: jest.fn(), listFriendBotSessions: jest.fn() },
}));

jest.mock('@/services/workspace/conversationFavoriteService', () => ({
  conversationFavoriteService: { toggle: jest.fn() },
}));

jest.mock('@/services/workspace/botSessionService', () => ({
  botSessionService: { createSession: jest.fn(), deleteSession: jest.fn() },
  splitBotId: (botId: string) => {
    const idx = botId.indexOf(':');
    return idx < 0
      ? { realBotId: botId, ownerId: undefined }
      : { realBotId: botId.slice(0, idx), ownerId: botId.slice(idx + 1) };
  },
  BOT_SESSION_PAGE_SIZE: 10,
}));

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const listManagedSessions = conversationService.listManagedSessions as unknown as jest.Mock;
const listFriendBotSessions = conversationService.listFriendBotSessions as unknown as jest.Mock;
const createSession = botSessionService.createSession as unknown as jest.Mock;
const toggleFavorite = conversationFavoriteService.toggle as unknown as jest.Mock;

const managedBot: ConversationBotView = {
  bot: {
    botId: 'bot-a:2088',
    realBotId: 'bot-a',
    ownerId: '2088',
    displayName: '管理 Bot',
    online: true,
    chatable: true,
  },
  section: 'managed',
};
const otherManagedBot: ConversationBotView = {
  bot: {
    botId: 'bot-b:2088',
    realBotId: 'bot-b',
    ownerId: '2088',
    displayName: '另一管理 Bot',
    online: true,
    chatable: true,
  },
  section: 'managed',
};
const friendBot: ConversationBotView = {
  bot: {
    botId: 'fb-1:327325',
    realBotId: 'fb-1',
    ownerId: '327325',
    displayName: '好友 Bot',
    online: true,
    chatable: true,
    isFriendBot: true,
  },
  section: 'friend',
};

const sessionOf = (sessionId: string, botId: string, favorite?: boolean) => ({
  sessionId,
  botId,
  title: '会话',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
  ...(favorite === undefined ? {} : { favorite }),
});
const okPage = (sessionIds: string[], botId: string, total = sessionIds.length, favorite?: boolean) => ({
  ok: true as const,
  data: { items: sessionIds.map((id) => sessionOf(id, botId, favorite)), total },
});
const emptyPage = okPage([], managedBot.bot.botId);

function deferred<T = any>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function renderSessions(options?: Partial<Parameters<typeof useConversationSessions>[0]>) {
  return renderHook((props) => useConversationSessions(props), {
    initialProps: {
      userId: '327325',
      managedBots: [managedBot],
      friendBots: [friendBot],
      ...options,
    },
  });
}

beforeEach(() => {
  listManagedSessions.mockReset();
  listFriendBotSessions.mockReset();
  createSession.mockReset();
  toggleFavorite.mockReset();
  (toast.success as jest.Mock).mockReset();
  (toast.error as jest.Mock).mockReset();
  listManagedSessions.mockResolvedValue(emptyPage);
  listFriendBotSessions.mockResolvedValue(okPage([], friendBot.bot.botId));
  createSession.mockResolvedValue({ ok: true, data: sessionOf('s-new', managedBot.bot.botId) });
  toggleFavorite.mockResolvedValue({ ok: true, data: true });
  useConversationStore.getState().reset();
});

describe('useConversationSessions session filters', () => {
  it('loads the first page of a managed Bot with the applied sessionKey', async () => {
    listManagedSessions.mockResolvedValue(okPage(['k1a'], managedBot.bot.botId));
    const { result } = renderSessions();

    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, 'agent:main:k1'));
    act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));

    await waitFor(() =>
      expect(listManagedSessions).toHaveBeenCalledWith(managedBot.bot, '327325', 'all', 1, 10, 'agent:main:k1'),
    );
    const view = await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId[managedBot.bot.botId];
      expect(current.sessions.items.map((item) => item.sessionId)).toEqual(['k1a']);
      return current;
    });
    expect(view.scope).toBe('all');
    expect(view.sessions.error).toBeNull();
  });

  it('carries the same sessionKey to load-more pages', async () => {
    listManagedSessions
      .mockResolvedValueOnce(okPage(['k1a', 'k1b'], managedBot.bot.botId, 25))
      .mockResolvedValueOnce(okPage(['k2a'], managedBot.bot.botId, 25));
    const { result } = renderSessions();

    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, 'agent:main:k1'));
    act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
    await waitFor(() =>
      expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId]?.sessions.items).toHaveLength(2),
    );

    await act(async () => result.current.loadMoreSessions(managedBot.bot.botId, 'managed', 'all'));

    expect(listManagedSessions).toHaveBeenLastCalledWith(managedBot.bot, '327325', 'all', 2, 10, 'agent:main:k1');
    const list = useConversationStore.getState().sessionsByBotId[managedBot.bot.botId].sessions;
    expect(list.items.map((item) => item.sessionId)).toEqual(['k1a', 'k1b', 'k2a']);
    expect(list.page).toBe(2);
    expect(list.hasMore).toBe(true);
  });

  it('restores the unfiltered first page after clearing the search', async () => {
    listManagedSessions
      .mockResolvedValueOnce(okPage(['k1a'], managedBot.bot.botId))
      .mockResolvedValueOnce(okPage(['s1'], managedBot.bot.botId));
    const { result } = renderSessions();

    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, 'agent:main:k1'));
    act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
    await waitFor(() =>
      expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId]?.sessions.items).toHaveLength(1),
    );

    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, ''));

    await waitFor(() =>
      expect(listManagedSessions).toHaveBeenLastCalledWith(managedBot.bot, '327325', 'all', 1, 10, ''),
    );
    await waitFor(() => {
      expect(
        useConversationStore
          .getState()
          .sessionsByBotId[managedBot.bot.botId].sessions.items.map((item) => item.sessionId),
      ).toEqual(['s1']);
    });
  });

  it('drops stale A→B→A responses and only writes the newest revision', async () => {
    const requests: ReturnType<typeof deferred>[] = [];
    listManagedSessions.mockImplementation(() => {
      const request = deferred();
      requests.push(request);
      return request.promise;
    });
    const { result } = renderSessions();

    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, 'agent:main:A'));
    act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, 'agent:main:B'));
    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, 'agent:main:A'));

    expect(requests).toHaveLength(3);
    await act(async () => {
      requests[0].resolve(okPage(['a-old'], managedBot.bot.botId));
      requests[1].resolve(okPage(['b-stale'], managedBot.bot.botId));
      await requests[0].promise;
      await requests[1].promise;
    });
    // 旧 A、B 响应都被代次拒绝:列表仍是新条件下的加载中空态,不能被旧数据复原。
    expect(
      useConversationStore
        .getState()
        .sessionsByBotId[managedBot.bot.botId].sessions.items.map((item) => item.sessionId),
    ).toEqual([]);

    await act(async () => {
      requests[2].resolve(okPage(['a-new'], managedBot.bot.botId));
      await requests[2].promise;
    });
    expect(
      useConversationStore
        .getState()
        .sessionsByBotId[managedBot.bot.botId].sessions.items.map((item) => item.sessionId),
    ).toEqual(['a-new']);
  });

  it('rejects responses captured by the previous user after a user switch', async () => {
    const requests: ReturnType<typeof deferred>[] = [];
    listManagedSessions.mockImplementation(() => {
      const request = deferred();
      requests.push(request);
      return request.promise;
    });
    const { result, rerender } = renderSessions();

    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, 'agent:main:k1'));
    act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
    expect(requests[0]).toBeDefined();
    const firstUserId = listManagedSessions.mock.calls[0][1];
    expect(firstUserId).toBe('327325');

    // 会话页在登录用户变化时整仓 reset;在此契约下旧在途响应不得再写回。
    rerender({ userId: 'user-2', managedBots: [managedBot], friendBots: [friendBot] });
    act(() => useConversationStore.getState().reset());

    await act(async () => {
      requests[0].resolve(okPage(['stale-1'], managedBot.bot.botId));
      await requests[0].promise;
    });
    expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId]).toBeUndefined();

    // 用户切换后同条件重拉走新用户身份;整仓 reset 已按约定清空过滤条件(请求不携带 key)。
    act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
    await waitFor(() => expect(requests.length).toBeGreaterThanOrEqual(2));
    expect(listManagedSessions.mock.calls[1][1]).toBe('user-2');
    expect(listManagedSessions.mock.calls[1][5]).toBe('');
    await act(async () => {
      requests[1].resolve(okPage(['u2-1'], managedBot.bot.botId));
      await requests[1].promise;
    });
    const cache = useConversationStore.getState().sessionsByBotId[managedBot.bot.botId];
    expect(cache.sessions.items.map((item) => item.sessionId)).toEqual(['u2-1']);
  });

  it('keeps the sessionKey while retrying a failed filtered first page', async () => {
    listManagedSessions
      .mockResolvedValueOnce({
        ok: false,
        error: { code: 'X', friendlyMessage: '加载会话失败', canRetry: true },
      })
      .mockResolvedValueOnce(okPage(['k1a'], managedBot.bot.botId));
    const { result } = renderSessions();

    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, 'agent:main:k1'));
    act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
    await waitFor(() => {
      expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId].sessions.error).toBe('加载会话失败');
    });

    act(() => result.current.retrySessions(managedBot.bot.botId, 'managed'));

    await waitFor(() =>
      expect(listManagedSessions).toHaveBeenNthCalledWith(2, managedBot.bot, '327325', 'all', 1, 10, 'agent:main:k1'),
    );
    await waitFor(() => {
      const list = useConversationStore.getState().sessionsByBotId[managedBot.bot.botId].sessions;
      expect(list.items.map((item) => item.sessionId)).toEqual(['k1a']);
      expect(list.error).toBeNull();
    });
  });

  it('applies filters per Bot without leaking keys between directories', async () => {
    listManagedSessions
      .mockResolvedValueOnce(okPage(['ka1'], managedBot.bot.botId))
      .mockResolvedValueOnce(okPage(['b1'], otherManagedBot.bot.botId));
    const { result } = renderSessions({ managedBots: [managedBot, otherManagedBot] });

    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, 'agent:main:k-a'));
    act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
    await waitFor(() =>
      expect(listManagedSessions).toHaveBeenCalledWith(managedBot.bot, '327325', 'all', 1, 10, 'agent:main:k-a'),
    );

    act(() => result.current.toggleBot(otherManagedBot.bot.botId, 'managed'));
    await waitFor(() =>
      expect(listManagedSessions).toHaveBeenLastCalledWith(otherManagedBot.bot, '327325', 'all', 1, 10, ''),
    );

    expect(
      useConversationStore
        .getState()
        .sessionsByBotId[managedBot.bot.botId].sessions.items.map((item) => item.sessionId),
    ).toEqual(['ka1']);
    expect(
      useConversationStore
        .getState()
        .sessionsByBotId[otherManagedBot.bot.botId].sessions.items.map((item) => item.sessionId),
    ).toEqual(['b1']);
  });

  it('reloads a managed scope switch as a fresh empty loading list instead of stale items', async () => {
    const favoritePage = deferred();
    listManagedSessions
      .mockResolvedValueOnce(okPage(['s1', 's2'], managedBot.bot.botId))
      .mockReturnValueOnce(favoritePage.promise);
    const { result } = renderSessions();

    act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
    await waitFor(() =>
      expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId]?.sessions.items).toHaveLength(2),
    );

    act(() => useConversationStore.getState().setManagedBotScope(managedBot.bot.botId, 'favorite'));

    expect(listManagedSessions).toHaveBeenLastCalledWith(managedBot.bot, '327325', 'favorite', 1, 10, '');
    const switching = useConversationStore.getState().sessionsByBotId[managedBot.bot.botId];
    // 更换条件后不把旧 all 结果当成 favorite 列表展示。
    expect(switching.scope).toBe('favorite');
    expect(switching.sessions.items).toEqual([]);
    expect(switching.sessions.loading).toBe(true);

    await act(async () => {
      favoritePage.resolve(okPage(['fav1'], managedBot.bot.botId));
      await favoritePage.promise;
    });
    expect(
      useConversationStore
        .getState()
        .sessionsByBotId[managedBot.bot.botId].sessions.items.map((item) => item.sessionId),
    ).toEqual(['fav1']);
  });

  it('loads a friend Bot favorite list through the favorites scope from the first page', async () => {
    listFriendBotSessions.mockResolvedValue(okPage(['fav1', 'fav2'], friendBot.bot.botId, 2, true));
    const { result } = renderSessions();

    act(() => useConversationStore.getState().setFriendBotScope(friendBot.bot.botId, 'favorite'));
    act(() => result.current.toggleBot(friendBot.bot.botId, 'friend'));

    await waitFor(() =>
      expect(listFriendBotSessions).toHaveBeenCalledWith(friendBot.bot, '327325', 1, 10, 'favorite', ''),
    );
    await waitFor(() => {
      const list = useConversationStore.getState().friendBotSessionsByBotId[friendBot.bot.botId];
      expect(list.items.map((item) => item.sessionId)).toEqual(['fav1', 'fav2']);
      expect(list.items.every((item) => item.favorite === true)).toBe(true);
    });
  });

  it('removes the unfavorited row in the friend favorite view and refills by loaded count', async () => {
    const seeded = firstPageOf(
      [sessionOf('fav1', friendBot.bot.botId, true), sessionOf('fav2', friendBot.bot.botId, true)],
      12,
    );
    const { result } = renderSessions();
    act(() => {
      const store = useConversationStore.getState();
      store.setFriendBotScope(friendBot.bot.botId, 'favorite');
      store.setFriendBotSessions(friendBot.bot.botId, seeded);
      store.selectConversation({
        botId: friendBot.bot.botId,
        section: 'friend',
        origin: 'mine',
        scope: 'favorite',
        friendUserId: null,
        sessionId: 'fav1',
      });
    });

    toggleFavorite.mockResolvedValue({ ok: true, data: false });
    await act(async () => {
      await result.current.favorites.toggleFavorite(friendBot.bot.botId, 'friend', 'fav1');
    });

    expect(toggleFavorite).toHaveBeenCalledWith(friendBot.bot, '327325', 'fav1', false);
    const after = useConversationStore.getState().friendBotSessionsByBotId[friendBot.bot.botId];
    expect(after.items.map((item) => item.sessionId)).toEqual(['fav2']);
    expect(after.total).toBe(11);
    expect(useConversationStore.getState().selectedSessionId).toBeNull();

    listFriendBotSessions.mockResolvedValue(okPage(['fav3'], friendBot.bot.botId, 11, true));
    await act(async () => result.current.loadMoreSessions(friendBot.bot.botId, 'friend', 'favorite'));
    // 收藏移除使后端 offset 前移,按已加载数量重取第 1 页补齐。
    expect(listFriendBotSessions).toHaveBeenLastCalledWith(friendBot.bot, '327325', 1, 10, 'favorite', '');
    const refilled = useConversationStore.getState().friendBotSessionsByBotId[friendBot.bot.botId];
    expect(refilled.items.map((item) => item.sessionId)).toEqual(['fav2', 'fav3']);
    expect(refilled.page).toBe(1);
    expect(refilled.hasMore).toBe(true);
  });

  it('blocks favorite writes on a friend Bot while its favorite page is loading more', async () => {
    const { result } = renderSessions();
    act(() => {
      const store = useConversationStore.getState();
      store.setFriendBotScope(friendBot.bot.botId, 'favorite');
      store.setFriendBotSessions(friendBot.bot.botId, {
        ...firstPageOf([sessionOf('fav1', friendBot.bot.botId, true)], 12),
        isLoadingMore: true,
      });
    });

    expect(result.current.favorites.isPending(friendBot.bot.botId, 'friend', 'fav1')).toBe(true);
    await act(async () => {
      await result.current.favorites.toggleFavorite(friendBot.bot.botId, 'friend', 'fav1');
    });
    expect(toggleFavorite).not.toHaveBeenCalled();
  });

  it('keeps the selected main conversation when the search filter changes', async () => {
    listManagedSessions.mockResolvedValue(okPage(['s1'], managedBot.bot.botId));
    const { result } = renderSessions();

    act(() => result.current.selectMineSession(managedBot.bot.botId, 's1'));
    await waitFor(() =>
      expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId]?.sessions.items).toHaveLength(1),
    );

    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, 'agent:main:zz'));
    listManagedSessions.mockResolvedValue(okPage(['zz1'], managedBot.bot.botId));
    await waitFor(() =>
      expect(listManagedSessions).toHaveBeenLastCalledWith(managedBot.bot, '327325', 'all', 1, 10, 'agent:main:zz'),
    );

    const store = useConversationStore.getState();
    expect(store.selectedBotId).toBe(managedBot.bot.botId);
    expect(store.selectedSessionId).toBe('s1');
  });

  it('normalizes a remembered friend favorite scope on engines without favorites support', async () => {
    const teclawFriend: ConversationBotView = {
      section: 'friend',
      bot: { ...friendBot.bot, engine: 'TEClaw' },
    };
    listFriendBotSessions.mockResolvedValue(okPage(['fs1'], friendBot.bot.botId));
    const { result } = renderSessions({ friendBots: [teclawFriend] });

    act(() => useConversationStore.getState().setFriendBotScope(friendBot.bot.botId, 'favorite'));
    await waitFor(() => expect(useConversationStore.getState().scopeByFriendBotId[friendBot.bot.botId]).toBe('all'));

    act(() => result.current.toggleBot(friendBot.bot.botId, 'friend'));
    await waitFor(() =>
      expect(listFriendBotSessions).toHaveBeenCalledWith(teclawFriend.bot, '327325', 1, 10, 'all', ''),
    );
    expect(listFriendBotSessions.mock.calls.every((call) => call[4] === 'all')).toBe(true);
  });

  it('creates a session without prepending it into a filtered list but still selects it', async () => {
    listManagedSessions.mockResolvedValue(okPage(['k1a'], managedBot.bot.botId));
    const { result } = renderSessions();

    act(() => useConversationStore.getState().setSessionKeyFilter(managedBot.bot.botId, 'agent:main:k1'));
    act(() => result.current.toggleBot(managedBot.bot.botId, 'managed'));
    await waitFor(() =>
      expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId]?.sessions.items).toHaveLength(1),
    );

    createSession.mockResolvedValue({ ok: true, data: sessionOf('s-new', managedBot.bot.botId) });
    await act(async () => result.current.createSession(managedBot.bot.botId));

    expect(createSession).toHaveBeenCalledWith(managedBot.bot, '327325');
    // 过滤中的列表不能混入不匹配的新会话。
    expect(
      useConversationStore
        .getState()
        .sessionsByBotId[managedBot.bot.botId].sessions.items.map((item) => item.sessionId),
    ).toEqual(['k1a']);
    expect(useConversationStore.getState().selectedSessionId).toBe('s-new');
    expect(toast.success).toHaveBeenCalledWith('会话已创建');
  });
});
