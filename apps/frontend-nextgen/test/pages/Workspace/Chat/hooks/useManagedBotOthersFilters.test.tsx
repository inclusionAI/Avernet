/** @jest-environment jsdom */
// 管理 Bot others 视角的过滤回归:分组首拉/重试/下一页均透传 sessionKey,旧目录/分组响应被代次丢弃。
import type { ConversationBotView } from '@/domain/conversation';
import { useManagedBotOthers } from '@/pages/Workspace/Chat/hooks/useManagedBotOthers';
import { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import { useConversationStore } from '@/stores/conversationStore';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/managedBotConversationService', () => ({
  managedBotConversationService: {
    loadFriendUsers: jest.fn(),
    listOtherSessions: jest.fn(),
    listOtherMessages: jest.fn(),
  },
}));

jest.mock('@/services/backendApi/collaboration/collaborationFriendConnectionController', () => ({
  listFriendConnections: jest.fn(),
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

const userIdOf = (id: string) => ({ userId: id, displayName: id });
const okUsers = (ids: string[]) => ({ ok: true as const, data: ids.map(userIdOf) });
/** 目录是好友资料的权威来源:displayName 与 userId 必须可区分。 */
const okNamedUsers = (entries: Array<[userId: string, displayName: string]>) => ({
  ok: true as const,
  data: entries.map(([userId, displayName]) => ({ userId, displayName })),
});
const okSessions = (sessionIds: string[], total = sessionIds.length, page = 1) => ({
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
    page,
    total,
    hasMore: 10 < total,
    loading: false,
    error: null,
    isLoadingMore: false,
    loadMoreError: null,
  },
});

function deferred<T = any>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function renderOthers(enabled = true) {
  return renderHook(() => useManagedBotOthers({ userId: '327325', managedBots: [managedBot], enabled }));
}

function enableOthers(botId = 'bot-a:2088') {
  act(() => {
    useConversationStore.getState().setManagedBotOrigin(botId, 'others');
    useConversationStore.getState().setExpandedBot(botId, true);
  });
}

beforeEach(() => {
  loadFriendUsers.mockReset();
  listOtherSessions.mockReset();
  loadFriendUsers.mockResolvedValue(okUsers([]));
  listOtherSessions.mockResolvedValue(okSessions([]));
  useConversationStore.getState().reset();
});

describe('useManagedBotOthers session filters', () => {
  it('reloads the directory and the expanded friend group with the Bot search key, keeping expansion intent', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['447147', '447148']));
    listOtherSessions.mockResolvedValue(okSessions(['s1']));
    const { result } = renderOthers();
    enableOthers();
    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(1));
    act(() => result.current.toggleFriend('bot-a:2088', '447147'));
    await waitFor(() => expect(listOtherSessions).toHaveBeenCalledWith(managedBot.bot, '447147', 1, 10, ''));
    await waitFor(() => {
      expect(useConversationStore.getState().sessionsByBotId['bot-a:2088'].friendGroups['447147'].state).toBe('loaded');
    });

    act(() => useConversationStore.getState().setSessionKeyFilter('bot-a:2088', 'agent:main:k1'));

    await waitFor(() =>
      expect(listOtherSessions).toHaveBeenLastCalledWith(managedBot.bot, '447147', 1, 10, 'agent:main:k1'),
    );
    // 缓存被过滤变化删除后目录按新条件重拉,展开意图仍保留。
    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(2));
    expect(useConversationStore.getState().expandedFriendUserIdsByBotId['bot-a:2088']['447147']).toBe(true);
    const view = await waitFor(() => {
      const current = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
      expect(current.friendDirectory.error).toBeNull();
      expect(current.friendGroups['447147'].state).toBe('loaded');
      return current;
    });
    expect(view.friendDirectory.items.map((user) => user.userId)).toEqual(['447147', '447148']);
  });

  it('keeps the key when retrying a failed friend group', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['447147']));
    listOtherSessions
      .mockResolvedValueOnce({
        ok: false,
        error: { code: 'X', friendlyMessage: '加载好友用户会话失败', canRetry: true },
      })
      .mockResolvedValueOnce(okSessions(['sk1']));
    const { result } = renderOthers();

    act(() => useConversationStore.getState().setSessionKeyFilter('bot-a:2088', 'agent:main:k1'));
    enableOthers();
    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(1));
    act(() => result.current.toggleFriend('bot-a:2088', '447147'));
    await waitFor(() => {
      expect(useConversationStore.getState().sessionsByBotId['bot-a:2088'].friendGroups['447147'].state).toBe('error');
    });

    act(() => result.current.retryFriendSessions('bot-a:2088', '447147'));

    await waitFor(() =>
      expect(listOtherSessions).toHaveBeenNthCalledWith(2, managedBot.bot, '447147', 1, 10, 'agent:main:k1'),
    );
    await waitFor(() => {
      const group = useConversationStore.getState().sessionsByBotId['bot-a:2088'].friendGroups['447147'];
      expect(group.state).toBe('loaded');
      expect(group.sessions.items.map((item) => item.sessionId)).toEqual(['sk1']);
    });
  });

  it('passes the key to the friend group next page', async () => {
    loadFriendUsers.mockResolvedValue(okUsers(['447147']));
    listOtherSessions.mockResolvedValueOnce(okSessions(['s1'], 25)).mockResolvedValueOnce(okSessions(['sk2'], 25, 2));
    const { result } = renderOthers();

    act(() => useConversationStore.getState().setSessionKeyFilter('bot-a:2088', 'agent:main:k1'));
    enableOthers();
    await waitFor(() => expect(loadFriendUsers).toHaveBeenCalledTimes(1));
    act(() => result.current.toggleFriend('bot-a:2088', '447147'));
    await waitFor(() => {
      const group = useConversationStore.getState().sessionsByBotId['bot-a:2088'].friendGroups['447147'];
      expect(group.state).toBe('loaded');
      expect(group.sessions.items).toHaveLength(1);
    });

    await act(async () => result.current.loadMoreFriendSessions('bot-a:2088', '447147'));

    expect(listOtherSessions).toHaveBeenNthCalledWith(2, managedBot.bot, '447147', 2, 10, 'agent:main:k1');
    const group = useConversationStore.getState().sessionsByBotId['bot-a:2088'].friendGroups['447147'];
    expect(group.sessions.items.map((item) => item.sessionId)).toEqual(['s1', 'sk2']);
    expect(group.sessions.page).toBe(2);
    expect(group.sessions.hasMore).toBe(true);
  });

  it('drops directory and group responses captured before the key change', async () => {
    const directoryRequests: ReturnType<typeof deferred>[] = [];
    const groupRequests: ReturnType<typeof deferred>[] = [];
    loadFriendUsers.mockImplementation(() => {
      const request = deferred();
      directoryRequests.push(request);
      return request.promise;
    });
    listOtherSessions.mockImplementation(() => {
      const request = deferred();
      groupRequests.push(request);
      return request.promise;
    });
    const { result } = renderOthers();

    enableOthers();
    act(() => result.current.toggleFriend('bot-a:2088', '447147'));
    act(() => useConversationStore.getState().setSessionKeyFilter('bot-a:2088', 'agent:main:k2'));

    // 同名旧请求不得阻塞新代次,目录与分组都按新条件重发。
    await waitFor(() => expect(directoryRequests).toHaveLength(2));
    await waitFor(() => expect(groupRequests).toHaveLength(2));

    await act(async () => {
      directoryRequests[0].resolve(okUsers(['447147']));
      groupRequests[0].resolve(okSessions(['stale-s1']));
      await directoryRequests[0].promise;
      await groupRequests[0].promise;
    });
    const switching = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
    // 旧响应被代次丢弃:目录与分组保持新条件下的加载中状态。
    expect(switching.friendDirectory.loading).toBe(true);
    expect(switching.friendDirectory.items).toEqual([]);
    expect(switching.friendGroups['447147'].sessions.loading).toBe(true);
    expect(switching.friendGroups['447147'].sessions.items).toEqual([]);

    await act(async () => {
      directoryRequests[1].resolve(okUsers(['447147', '447148']));
      groupRequests[1].resolve(okSessions(['fresh-s1']));
      await directoryRequests[1].promise;
      await groupRequests[1].promise;
    });
    const final = useConversationStore.getState().sessionsByBotId['bot-a:2088'];
    expect(final.friendDirectory.items.map((user) => user.userId)).toEqual(['447147', '447148']);
    expect(final.friendGroups['447147'].state).toBe('loaded');
    expect(final.friendGroups['447147'].sessions.items.map((item) => item.sessionId)).toEqual(['fresh-s1']);
  });

  it('restores the authoritative friend profile from a late directory response after a search-triggered group reload', async () => {
    const directoryRequests: ReturnType<typeof deferred>[] = [];
    const groupRequests: ReturnType<typeof deferred>[] = [];
    loadFriendUsers.mockImplementation(() => {
      const request = deferred();
      directoryRequests.push(request);
      return request.promise;
    });
    listOtherSessions.mockImplementation(() => {
      const request = deferred();
      groupRequests.push(request);
      return request.promise;
    });
    const { result } = renderOthers();
    const botId = 'bot-a:2088';

    // 基线:目录先落地,分组使用真实资料。
    enableOthers();
    await act(async () => {
      directoryRequests[0].resolve(okNamedUsers([['447147', '张小满']]));
      await directoryRequests[0].promise;
    });
    act(() => result.current.toggleFriend(botId, '447147'));
    await waitFor(() => expect(groupRequests).toHaveLength(1));
    await act(async () => {
      groupRequests[0].resolve(okSessions(['s1']));
      await groupRequests[0].promise;
    });
    let view = useConversationStore.getState().sessionsByBotId[botId];
    expect(view.friendGroups['447147'].friend.displayName).toBe('张小满');

    // 应用搜索:缓存失效后,分组先于目录重建,先以 userId 兜底。
    act(() => useConversationStore.getState().setSessionKeyFilter(botId, 'agent:main:k1'));
    await waitFor(() => expect(directoryRequests).toHaveLength(2));
    await waitFor(() => expect(groupRequests).toHaveLength(2));
    await act(async () => {
      groupRequests[1].resolve(okSessions(['sk1']));
      await groupRequests[1].promise;
    });
    view = useConversationStore.getState().sessionsByBotId[botId];
    expect(view.friendGroups['447147'].friend.displayName).toBe('447147');

    // 迟到的目录响应必须用权威资料替换兜底资料,并保留分组的会话/加载/展开状态。
    await act(async () => {
      directoryRequests[1].resolve(okNamedUsers([['447147', '王小虎']]));
      await directoryRequests[1].promise;
    });
    view = useConversationStore.getState().sessionsByBotId[botId];
    expect(view.friendGroups['447147'].friend.displayName).toBe('王小虎');
    expect(view.friendGroups['447147'].friend.userId).toBe('447147');
    expect(view.friendGroups['447147'].state).toBe('loaded');
    expect(view.friendGroups['447147'].sessions.items.map((item) => item.sessionId)).toEqual(['sk1']);
    expect(view.friendGroups['447147'].expanded).toBe(true);

    // 清空搜索:同一重拉路径再次兜底后,必须再次被目录权威资料修复。
    act(() => useConversationStore.getState().setSessionKeyFilter(botId, ''));
    await waitFor(() => expect(directoryRequests).toHaveLength(3));
    await waitFor(() => expect(groupRequests).toHaveLength(3));
    await act(async () => {
      groupRequests[2].resolve(okSessions(['s1']));
      await groupRequests[2].promise;
    });
    view = useConversationStore.getState().sessionsByBotId[botId];
    expect(view.friendGroups['447147'].friend.displayName).toBe('447147');
    await act(async () => {
      directoryRequests[2].resolve(okNamedUsers([['447147', '张小满']]));
      await directoryRequests[2].promise;
    });
    view = useConversationStore.getState().sessionsByBotId[botId];
    expect(view.friendGroups['447147'].friend.displayName).toBe('张小满');
    expect(view.friendGroups['447147'].state).toBe('loaded');
    expect(view.friendGroups['447147'].sessions.items.map((item) => item.sessionId)).toEqual(['s1']);
    expect(view.friendGroups['447147'].expanded).toBe(true);
  });
});
