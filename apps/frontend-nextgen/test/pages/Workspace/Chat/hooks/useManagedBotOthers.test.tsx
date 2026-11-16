/** @jest-environment jsdom */
import type { ConversationBotView } from '@/domain/conversation';
import { useManagedBotOthers } from '@/pages/Workspace/Chat/hooks/useManagedBotOthers';
import { listFriendConnections } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import { useConversationStore } from '@/stores/conversationStore';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/managedBotConversationService', () => ({
  managedBotConversationService: {
    loadFriendUsers: require('jest-mock').fn(),
    listOtherSessions: require('jest-mock').fn(),
    listOtherMessages: require('jest-mock').fn(),
  },
}));

jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController', () => ({
  listFriendConnections: require('jest-mock').fn(),
}));

const loadFriendUsers = managedBotConversationService.loadFriendUsers as unknown as jest.Mock;
const listOtherSessions = managedBotConversationService.listOtherSessions as unknown as jest.Mock;

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
const otherBot: ConversationBotView = {
  bot: {
    botId: 'bot-b:2088',
    realBotId: 'bot-b',
    ownerId: '2088',
    displayName: '另一 Bot',
    online: true,
    chatable: true,
  },
  section: 'managed',
};
const userIdOf = (id: string) => ({ userId: id, displayName: id });
const okUsers = (ids: string[]) => ({ ok: true as const, data: ids.map(userIdOf) });
const okSessions = (sessionIds: string[], total = sessionIds.length) => ({
  ok: true as const,
  data: {
    items: sessionIds.map((sessionId) => ({
      sessionId,
      botId: 'bot-a:2088',
      title: 's',
      messageCount: 0,
      gmtModified: '',
      gmtCreate: '',
    })),
    page: 1,
    total,
    hasMore: 10 < total,
    loading: false,
    error: null,
    isLoadingMore: false,
    loadMoreError: null,
  },
});

type OtherSessionsResult = Awaited<ReturnType<typeof managedBotConversationService.listOtherSessions>>;
type FriendUsersResult = Awaited<ReturnType<typeof managedBotConversationService.loadFriendUsers>>;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function enableOthers(botId = 'bot-a:2088') {
  act(() => {
    useConversationStore.getState().setManagedBotOrigin(botId, 'others');
    useConversationStore.getState().setExpandedBot(botId, true);
  });
}

beforeEach(() => {
  // require('jest-mock').fn() 的 mock 不归全局 clearAllMocks 管,逐个 reset。
  loadFriendUsers.mockReset();
  listOtherSessions.mockReset();
  // 未在用例内单独 stub 时给兜底实现,避免空 mock 返回 undefined。
  loadFriendUsers.mockResolvedValue(okUsers([]));
  listOtherSessions.mockResolvedValue(okSessions([]));
  useConversationStore.getState().reset();
});

describe('useManagedBotOthers', () => {
  it('loads others directory only after a managed Bot selects others', () => {
    const { result } = renderHook(() =>
      useManagedBotOthers({
        userId: '327325',
        managedBots: [managedBot],
        enabled: false,
      }),
    );
    expect(listFriendConnections).not.toHaveBeenCalled();
    act(() => result.current.toggleFriend('bot-a:2088', '447147'));
    expect(listFriendConnections).not.toHaveBeenCalled();
    expect(loadFriendUsers).not.toHaveBeenCalled();
  });

  it('does not load when the effective origin is mine', () => {
    loadFriendUsers.mockResolvedValue(okUsers(['447147']));
    renderHook(() => useManagedBotOthers({ userId: '327325', managedBots: [managedBot], enabled: true }));

    act(() => useConversationStore.getState().setExpandedBot('bot-a:2088', true));
    expect(loadFriendUsers).not.toHaveBeenCalled();
  });

  it('does not use the ordinary friend directory for a managed Coding Bot in others', async () => {
    const codingBot: ConversationBotView = {
      ...managedBot,
      bot: {
        ...managedBot.bot,
        engine: 'claude_code',
        isAgentCodingBot: true,
      },
    };
    renderHook(() => useManagedBotOthers({ userId: '327325', managedBots: [codingBot], enabled: true }));

    enableOthers();
    await act(async () => Promise.resolve());

    expect(loadFriendUsers).not.toHaveBeenCalled();
    expect(listOtherSessions).not.toHaveBeenCalled();
  });

  it('loads the friend-user directory once for an expanded managed Bot in others', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['447147', '447148']));
    const { result } = renderHook(() =>
      useManagedBotOthers({ userId: '327325', managedBots: [managedBot], enabled: true }),
    );
    enableOthers();

    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(1));
    expect(loadFriendUsers).toHaveBeenCalledWith(managedBot.bot, expect.anything());
    const view = await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
      expect(current.friendDirectory.items).toHaveLength(2);
      return current;
    });
    expect(view.origin).toBe('others');
    expect(view.friendGroups['447147'].state).toBe('unloaded');
    expect(view.friendGroups['447148'].state).toBe('unloaded');
    expect(view.friendGroups['447147'].expanded).toBe(false);
    expect(result.current.toggleFriend).toBeInstanceOf(Function);
  });

  it('loads a friend session list only when the friend group expands and keeps the rest unloaded', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['447147', '447148']));
    listOtherSessions.mockResolvedValue(okSessions(['s1']));
    const { result } = renderHook(() =>
      useManagedBotOthers({ userId: '327325', managedBots: [managedBot], enabled: true }),
    );
    enableOthers();
    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(1));

    act(() => result.current.toggleFriend('bot-a:2088', '447147'));

    await waitFor(() => expect(listOtherSessions).toHaveBeenCalledWith(managedBot.bot, '447147', 1, 10, ''));
    const view = await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
      expect(current.friendGroups['447147'].state).toBe('loaded');
      return current;
    });
    expect(view.friendGroups['447147'].sessions.items[0]?.sessionId).toBe('s1');
    expect(view.friendGroups['447147'].expanded).toBe(true);
    // 未加载的好友分组被保留，直到首次响应确认为空。
    expect(view.friendGroups['447148'].state).toBe('unloaded');
    expect(listOtherSessions).toHaveBeenCalledTimes(1);

    // 再触发同好友展开不重复请求。
    act(() => result.current.toggleFriend('bot-a:2088', '447147'));
    act(() => result.current.toggleFriend('bot-a:2088', '447147'));
    await act(async () => {
      await Promise.resolve();
    });
    expect(listOtherSessions).toHaveBeenCalledTimes(1);
  });

  it('keeps a failed friend group isolated and retries only that friend', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['447147', '447148']));
    listOtherSessions
      .mockResolvedValueOnce({
        ok: false,
        error: { code: 'X', friendlyMessage: '加载好友用户会话失败', canRetry: true },
      })
      .mockResolvedValue(okSessions(['s1']));
    const { result } = renderHook(() =>
      useManagedBotOthers({ userId: '327325', managedBots: [managedBot], enabled: true }),
    );
    enableOthers();
    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(1));

    act(() => result.current.toggleFriend('bot-a:2088', '447147'));
    const failed = await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
      expect(current.friendGroups['447147'].state).toBe('error');
      return current;
    });
    expect(failed.friendGroups['447147'].error).toBe('加载好友用户会话失败');
    expect(failed.friendGroups['447148'].state).toBe('unloaded');

    act(() => result.current.retryFriendSessions('bot-a:2088', '447147'));
    await waitFor(() => expect(listOtherSessions).toHaveBeenCalledTimes(2));
    const cached = await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
      expect(current.friendGroups['447147'].state).toBe('loaded');
      return current.friendGroups['447147'];
    });
    expect(cached.sessions.items[0]?.sessionId).toBe('s1');
  });

  it('retries only the directory request on retryDirectory', async () => {
    loadFriendUsers
      .mockResolvedValueOnce({ ok: false, error: { code: 'X', friendlyMessage: '加载好友用户失败', canRetry: true } })
      .mockResolvedValue(okUsers(['447147']));
    const { result } = renderHook(() =>
      useManagedBotOthers({ userId: '327325', managedBots: [managedBot], enabled: true }),
    );
    enableOthers();
    const failed = await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
      expect(current.friendDirectory.error).toBe('加载好友用户失败');
      return current;
    });
    expect(failed.friendDirectory.loading).toBe(false);

    act(() => result.current.retryDirectory('bot-a:2088'));
    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(2));
    await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
      expect(current.friendDirectory.items).toHaveLength(1);
      expect(current.friendDirectory.error).toBeNull();
    });
    expect(listOtherSessions).not.toHaveBeenCalled();
  });

  it('cannot let a stale friend response overwrite the retried friend list', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['447147', '447148']));
    const first = deferred<OtherSessionsResult>();
    listOtherSessions.mockImplementationOnce(() => first.promise).mockResolvedValueOnce(okSessions(['s-2']));
    const { result } = renderHook(() =>
      useManagedBotOthers({ userId: '327325', managedBots: [managedBot], enabled: true }),
    );
    enableOthers();
    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(1));

    act(() => result.current.toggleFriend('bot-a:2088', '447147'));
    // 首个请求在途时重试：重试响应先返回，第一条必须作废。
    act(() => result.current.retryFriendSessions('bot-a:2088', '447147'));
    const retried = await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
      expect(current.friendGroups['447147'].sessions.items[0]?.sessionId).toBe('s-2');
      return current;
    });
    expect(retried.friendGroups['447147'].state).toBe('loaded');

    await act(async () => {
      first.resolve(okSessions(['s-1-old']));
      await first.promise;
    });
    const final = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
    expect(final.friendGroups['447147'].sessions.items[0]?.sessionId).toBe('s-2');
    expect(final.friendGroups['447148'].state).toBe('unloaded');
  });

  it('writes each Bot directory response into its own Bot cache', async () => {
    const first = deferred<FriendUsersResult>();
    loadFriendUsers.mockImplementationOnce(() => first.promise).mockResolvedValueOnce(okUsers(['555001']));
    renderHook(() => useManagedBotOthers({ userId: '327325', managedBots: [managedBot, otherBot], enabled: true }));
    enableOthers('bot-a:2088');
    enableOthers('bot-b:2088');
    await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId['bot-b:2088'];
      expect(current.friendDirectory.items.map((user) => user.userId)).toEqual(['555001']);
    });

    await act(async () => {
      first.resolve(okUsers(['444001']));
      await first.promise;
    });
    // bot-a 的响应只落位 bot-a 缓存，bot-b 不被覆盖。
    expect(
      useConversationStore.getState().sessionsByBotId['bot-a:2088'].friendDirectory.items.map((u) => u.userId),
    ).toEqual(['444001']);
    expect(
      useConversationStore.getState().sessionsByBotId['bot-b:2088'].friendDirectory.items.map((u) => u.userId),
    ).toEqual(['555001']);
  });

  it('loadMoreFriendSessions appends the next friend page', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['447147']));
    listOtherSessions.mockResolvedValueOnce(okSessions(['s1'], 25)).mockResolvedValueOnce({
      ok: true as const,
      data: {
        items: [{ sessionId: 's2', botId: 'bot-a:2088', title: 's', messageCount: 0, gmtModified: '', gmtCreate: '' }],
        page: 2,
        total: 25,
        hasMore: true,
        loading: false,
        error: null,
        isLoadingMore: false,
        loadMoreError: null,
      },
    });
    const { result } = renderHook(() =>
      useManagedBotOthers({ userId: '327325', managedBots: [managedBot], enabled: true }),
    );
    enableOthers();
    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(1));
    act(() => result.current.toggleFriend('bot-a:2088', '447147'));
    await waitFor(() => {
      const group = useConversationStore.getState().sessionsByBotId['bot-a:2088'].friendGroups['447147'];
      expect(group.state).toBe('loaded');
      expect(group.sessions.items).toHaveLength(1);
    });

    await act(async () => result.current.loadMoreFriendSessions('bot-a:2088', '447147'));

    expect(listOtherSessions).toHaveBeenLastCalledWith(managedBot.bot, '447147', 2, 10, '');
    const group = useConversationStore.getState().sessionsByBotId['bot-a:2088'].friendGroups['447147'];
    expect(group.sessions.items.map((item) => item.sessionId)).toEqual(['s1', 's2']);
    expect(group.sessions.page).toBe(2);
    expect(group.sessions.hasMore).toBe(true);
  });

  it('stops loading when disabled', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['447147']));
    const { result, rerender } = renderHook(
      (props: { enabled: boolean }) =>
        useManagedBotOthers({ userId: '327325', managedBots: [managedBot], enabled: props.enabled }),
      { initialProps: { enabled: true } },
    );
    enableOthers();
    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(1));

    rerender({ enabled: false });
    act(() => result.current.toggleFriend('bot-a:2088', '447147'));
    expect(listOtherSessions).not.toHaveBeenCalled();
  });
});

describe.each(['managed', 'team'] as const)('%s others directory excludes the viewer', (section) => {
  it.each(['327325', 'human_327325', 'user_id:327325'])(
    'filters the signed-in user %s by ID, not by display name or Bot owner',
    async (userId) => {
      const view: ConversationBotView = {
        ...managedBot,
        section,
        bot: { ...managedBot.bot, isTeamBot: section === 'team' },
      };
      loadFriendUsers.mockResolvedValue({
        ok: true,
        data: ['327325', '2088', '447147'].map((id) => ({ userId: id, displayName: '同名用户' })),
      });
      renderHook(() => useManagedBotOthers({ userId, managedBots: [view], enabled: true }));
      enableOthers();

      await waitFor(() => {
        const cached = useConversationStore.getState().sessionsByBotId[view.bot.botId];
        expect(cached?.friendDirectory.items).toEqual([
          { userId: '2088', displayName: '同名用户' },
          { userId: '447147', displayName: '同名用户' },
        ]);
        expect(Object.keys(cached.friendGroups)).toEqual(['2088', '447147']);
      });
      expect(loadFriendUsers).toHaveBeenCalledTimes(1);
    },
  );
});

describe('others self-only and stale group handling', () => {
  it('treats a self-only friend directory as successfully empty', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['327325']));
    renderHook(() => useManagedBotOthers({ userId: '327325', managedBots: [managedBot], enabled: true }));
    enableOthers();

    await waitFor(() => {
      const cached = useConversationStore.getState().sessionsByBotId[managedBot.bot.botId];
      expect(cached?.friendDirectory).toEqual({ items: [], loading: false, error: null });
      expect(cached.friendGroups).toEqual({});
    });
    expect(loadFriendUsers).toHaveBeenCalledTimes(1);
    expect(listOtherSessions).not.toHaveBeenCalled();
  });

  it('removes a legacy self group on directory retry without changing other cached sessions', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['327325', '447147']));
    const { result } = renderHook(() =>
      useManagedBotOthers({ userId: '327325', managedBots: [managedBot], enabled: true }),
    );
    enableOthers();
    await waitFor(() =>
      expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId]?.friendDirectory.loading).toBe(
        false,
      ),
    );
    const cached = useConversationStore.getState().sessionsByBotId[managedBot.bot.botId];
    const otherGroup = {
      ...cached.friendGroups['447147'],
      state: 'loaded' as const,
      sessions: okSessions(['other-session']).data,
    };
    act(() =>
      useConversationStore.getState().setManagedBotCache(managedBot.bot.botId, {
        ...cached,
        friendGroups: {
          '447147': otherGroup,
          '327325': { ...otherGroup, friend: userIdOf('327325') },
        },
      }),
    );
    act(() => result.current.retryDirectory(managedBot.bot.botId));

    await waitFor(() => {
      const refreshed = useConversationStore.getState().sessionsByBotId[managedBot.bot.botId];
      expect(loadFriendUsers).toHaveBeenCalledTimes(2);
      expect(refreshed.friendDirectory.loading).toBe(false);
      expect(refreshed.friendGroups['327325']).toBeUndefined();
      expect(refreshed.friendGroups['447147']).toEqual(otherGroup);
    });
  });

  it('does not request the viewer sessions from a stale expanded self group or retry', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['327325', '447147']));
    act(() => useConversationStore.getState().setExpandedFriend(managedBot.bot.botId, '327325', true));
    const { result } = renderHook(() =>
      useManagedBotOthers({ userId: 'human_327325', managedBots: [managedBot], enabled: true }),
    );
    enableOthers();
    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(1));
    await act(async () => Promise.resolve());
    act(() => result.current.retryFriendSessions(managedBot.bot.botId, '327325'));
    expect(listOtherSessions).not.toHaveBeenCalled();
    expect(
      useConversationStore.getState().sessionsByBotId[managedBot.bot.botId].friendGroups['327325'],
    ).toBeUndefined();
  });
});

it('excludes the newly signed-in user when reusing an already loaded directory', async () => {
  loadFriendUsers.mockResolvedValue(okUsers(['447147', '555001']));
  const { rerender } = renderHook(
    ({ userId }) => useManagedBotOthers({ userId, managedBots: [managedBot], enabled: true }),
    { initialProps: { userId: '327325' } },
  );
  enableOthers();
  await waitFor(() =>
    expect(useConversationStore.getState().sessionsByBotId[managedBot.bot.botId]?.friendDirectory.items).toHaveLength(
      2,
    ),
  );

  rerender({ userId: 'human_447147' });

  await waitFor(() => {
    const cached = useConversationStore.getState().sessionsByBotId[managedBot.bot.botId];
    expect(cached.friendDirectory.items).toEqual([userIdOf('555001')]);
    expect(cached.friendGroups['447147']).toBeUndefined();
  });
  expect(loadFriendUsers).toHaveBeenCalledTimes(1);
});
