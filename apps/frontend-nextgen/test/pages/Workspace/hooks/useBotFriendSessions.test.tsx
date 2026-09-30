/** @jest-environment jsdom */
import { useBotFriendSessions } from '@/pages/Workspace/hooks/useBotFriendSessions';
import { botFriendConversationService } from '@/services/workspace/botFriendConversationService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botFriendConversationService', () => ({
  BOT_FRIEND_SESSION_PAGE_SIZE: 10,
  botFriendConversationService: {
    listSessionsPage: require('jest-mock').fn(),
  },
}));

const service = botFriendConversationService as jest.Mocked<typeof botFriendConversationService>;
const humanFriend = {
  actorType: 'human' as const,
  actorId: '447147',
  queryId: 'human_447147',
  displayName: '风太',
  online: false,
  detailsResolved: true,
};
const session = (id: string) => ({
  sessionId: id,
  friendUserId: '447147',
  title: id,
  messageCount: 0,
  gmtCreate: '2026-09-15T10:00:00Z',
  gmtModified: '2026-09-16T10:00:00Z',
});

function success(items: ReturnType<typeof session>[], total = items.length, page = 1) {
  return { ok: true as const, data: { items, total, page, pageSize: 10, hasMore: page * 10 < total } };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

beforeEach(() => {
  jest.clearAllMocks();
  service.listSessionsPage.mockReset();
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.setState({
    identities: [{ id: 'bot-a:327325', kind: 'bot', displayName: '皮皮虾', online: true }],
    activeIdentityId: 'bot-a:327325',
    view: 'chat',
  });
});

describe('useBotFriendSessions', () => {
  it('loads sessions only after the Human friend is expanded and selects explicitly', async () => {
    service.listSessionsPage.mockResolvedValue(success([session('session-1')]));
    const { result } = renderHook(() =>
      useBotFriendSessions({
        botIdentityId: 'bot-a:327325',
        humanFriends: [humanFriend],
        directorySettled: true,
        directoryError: null,
      }),
    );

    expect(service.listSessionsPage).not.toHaveBeenCalled();
    act(() => result.current.toggleFriend('447147'));
    await waitFor(() => expect(result.current.sessions).toEqual([session('session-1')]));
    expect(result.current.selectedSession).toBeNull();

    act(() => result.current.selectSession('session-1'));
    expect(useWorkspaceStore.getState().selectedFriendUserSessionId).toBe('session-1');
    expect(result.current.selectedSession?.sessionId).toBe('session-1');
  });

  it('collapses the same friend and clears the selected session', async () => {
    service.listSessionsPage.mockResolvedValue(success([session('session-1')]));
    const { result } = renderHook(() =>
      useBotFriendSessions({
        botIdentityId: 'bot-a:327325',
        humanFriends: [humanFriend],
        directorySettled: true,
        directoryError: null,
      }),
    );

    act(() => result.current.toggleFriend('447147'));
    await waitFor(() => expect(result.current.sessions).toHaveLength(1));
    act(() => result.current.selectSession('session-1'));
    act(() => result.current.toggleFriend('447147'));

    expect(useWorkspaceStore.getState().expandedFriendUserId).toBeNull();
    expect(useWorkspaceStore.getState().selectedFriendUserSessionId).toBeNull();
  });

  it('appends unique sessions and keeps first-page data when load more fails', async () => {
    service.listSessionsPage
      .mockResolvedValueOnce(success([session('s1'), session('s2')], 25, 1))
      .mockResolvedValueOnce(success([session('s2'), session('s3')], 25, 2))
      .mockResolvedValueOnce({
        ok: false,
        error: { code: 'LOAD_MORE_FAILED', friendlyMessage: '加载更多失败', canRetry: true },
      });
    useWorkspaceStore.setState({ expandedFriendUserId: '447147' });
    const { result } = renderHook(() =>
      useBotFriendSessions({
        botIdentityId: 'bot-a:327325',
        humanFriends: [humanFriend],
        directorySettled: true,
        directoryError: null,
      }),
    );
    await waitFor(() => expect(result.current.sessions.map((item) => item.sessionId)).toEqual(['s1', 's2']));

    await act(async () => result.current.loadMore());
    expect(result.current.sessions.map((item) => item.sessionId)).toEqual(['s1', 's2', 's3']);

    await act(async () => result.current.loadMore());
    expect(result.current.sessions.map((item) => item.sessionId)).toEqual(['s1', 's2', 's3']);
    expect(result.current.loadMoreError).toBe('加载更多失败');
  });

  it('deduplicates concurrent load-more requests', async () => {
    const pending = deferred<Awaited<ReturnType<typeof service.listSessionsPage>>>();
    service.listSessionsPage
      .mockResolvedValueOnce(success([session('s1')], 25, 1))
      .mockReturnValueOnce(pending.promise);
    useWorkspaceStore.setState({ expandedFriendUserId: '447147' });
    const { result } = renderHook(() =>
      useBotFriendSessions({
        botIdentityId: 'bot-a:327325',
        humanFriends: [humanFriend],
        directorySettled: true,
        directoryError: null,
      }),
    );
    await waitFor(() => expect(result.current.hasMore).toBe(true));

    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => {
      first = result.current.loadMore();
      second = result.current.loadMore();
    });
    expect(service.listSessionsPage).toHaveBeenCalledTimes(2);

    await act(async () => {
      pending.resolve(success([session('s2')], 25, 2));
      await Promise.all([first, second]);
    });
    expect(result.current.sessions.map((item) => item.sessionId)).toEqual(['s1', 's2']);
  });

  it('preserves URL-prefilled selection until the directory is settled', () => {
    useWorkspaceStore.setState({ expandedFriendUserId: '447147', selectedFriendUserSessionId: 'session-url' });
    renderHook(() =>
      useBotFriendSessions({
        botIdentityId: 'bot-a:327325',
        humanFriends: [],
        directorySettled: false,
        directoryError: null,
      }),
    );

    expect(useWorkspaceStore.getState().expandedFriendUserId).toBe('447147');
    expect(useWorkspaceStore.getState().selectedFriendUserSessionId).toBe('session-url');
  });

  it('clears a dangling friend only after a successful settled directory', async () => {
    useWorkspaceStore.setState({ expandedFriendUserId: 'missing', selectedFriendUserSessionId: 'session-url' });
    renderHook(() =>
      useBotFriendSessions({
        botIdentityId: 'bot-a:327325',
        humanFriends: [humanFriend],
        directorySettled: true,
        directoryError: null,
      }),
    );

    await waitFor(() => expect(useWorkspaceStore.getState().expandedFriendUserId).toBeNull());
    expect(useWorkspaceStore.getState().selectedFriendUserSessionId).toBeNull();
  });

  it('clears a URL-prefilled Session after all pages confirm it no longer exists', async () => {
    service.listSessionsPage.mockResolvedValue(success([session('existing')], 1, 1));
    useWorkspaceStore.setState({
      expandedFriendUserId: '447147',
      selectedFriendUserSessionId: 'missing-session',
    });

    renderHook(() =>
      useBotFriendSessions({
        botIdentityId: 'bot-a:327325',
        humanFriends: [humanFriend],
        directorySettled: true,
        directoryError: null,
      }),
    );

    await waitFor(() => expect(useWorkspaceStore.getState().selectedFriendUserSessionId).toBeNull());
  });

  it('ignores a late session response after switching identities', async () => {
    const first = deferred<Awaited<ReturnType<typeof service.listSessionsPage>>>();
    service.listSessionsPage
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce(success([session('new-session')]));
    useWorkspaceStore.setState({ expandedFriendUserId: '447147' });
    const { result, rerender } = renderHook(
      ({ botIdentityId }) =>
        useBotFriendSessions({
          botIdentityId,
          humanFriends: [humanFriend],
          directorySettled: true,
          directoryError: null,
        }),
      { initialProps: { botIdentityId: 'bot-old:1' } },
    );

    rerender({ botIdentityId: 'bot-new:2' });
    await waitFor(() => expect(result.current.sessions[0]?.sessionId).toBe('new-session'));
    await act(async () => {
      first.resolve(success([session('old-session')]));
      await first.promise;
    });
    expect(result.current.sessions[0]?.sessionId).toBe('new-session');
  });
});
