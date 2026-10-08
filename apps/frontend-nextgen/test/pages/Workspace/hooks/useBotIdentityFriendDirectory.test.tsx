/** @jest-environment jsdom */
import { useBotIdentityFriendDirectory } from '@/pages/Workspace/hooks/useBotIdentityFriendDirectory';
import { botFriendConversationService } from '@/services/workspace/botFriendConversationService';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botFriendConversationService', () => ({
  botFriendConversationService: {
    loadDirectory: require('jest-mock').fn(),
    loadSection: require('jest-mock').fn(),
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
const botFriend = {
  actorType: 'bot' as const,
  actorId: 'bot-b:100',
  queryId: 'bot-b:100',
  displayName: '皮皮虾',
  online: true,
  detailsResolved: true,
  disabledReason: '暂不支持查看 Bot 好友对话',
};
const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, pageSize: 100, hasMore: false });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

beforeEach(() => {
  jest.clearAllMocks();
  service.loadDirectory.mockReset();
  service.loadSection.mockReset();
});

describe('useBotIdentityFriendDirectory', () => {
  it('loads Human and Bot friend sections from the combined directory request', async () => {
    service.loadDirectory.mockResolvedValue({
      human: { ok: true, data: page([humanFriend]) },
      bot: { ok: true, data: page([botFriend]) },
    });

    const { result } = renderHook(() => useBotIdentityFriendDirectory('bot-a:327325', true));

    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(result.current.humanFriends).toEqual([humanFriend]);
    expect(result.current.botFriends).toEqual([botFriend]);
    expect(result.current.humanError).toBeNull();
    expect(result.current.botError).toBeNull();
  });

  it('does not request when disabled and clears prior results', async () => {
    service.loadDirectory.mockResolvedValue({
      human: { ok: true, data: page([humanFriend]) },
      bot: { ok: true, data: page([botFriend]) },
    });
    const { result, rerender } = renderHook(({ enabled }) => useBotIdentityFriendDirectory('bot-a:327325', enabled), {
      initialProps: { enabled: true },
    });
    await waitFor(() => expect(result.current.humanFriends).toHaveLength(1));

    rerender({ enabled: false });

    expect(result.current.humanFriends).toEqual([]);
    expect(result.current.botFriends).toEqual([]);
    expect(result.current.settled).toBe(false);
  });

  it('ignores a late response from the previous identity', async () => {
    const first = deferred<Awaited<ReturnType<typeof service.loadDirectory>>>();
    service.loadDirectory.mockReturnValueOnce(first.promise).mockResolvedValueOnce({
      human: {
        ok: true,
        data: page([{ ...humanFriend, actorId: 'new', queryId: 'human_new', displayName: '新用户' }]),
      },
      bot: { ok: true, data: page([]) },
    });

    const { result, rerender } = renderHook(({ identityId }) => useBotIdentityFriendDirectory(identityId, true), {
      initialProps: { identityId: 'bot-old:1' },
    });
    rerender({ identityId: 'bot-new:2' });
    await waitFor(() => expect(result.current.humanFriends[0]?.actorId).toBe('new'));

    await act(async () => {
      first.resolve({
        human: { ok: true, data: page([humanFriend]) },
        bot: { ok: true, data: page([botFriend]) },
      });
      await first.promise;
    });

    expect(result.current.humanFriends[0]?.actorId).toBe('new');
  });

  it('keeps section errors independent and retries only the requested section', async () => {
    service.loadDirectory.mockResolvedValue({
      human: {
        ok: false,
        error: { code: 'HUMAN_FAILED', friendlyMessage: '好友用户加载失败', canRetry: true },
      },
      bot: { ok: true, data: page([botFriend]) },
    });
    service.loadSection.mockResolvedValue({ ok: true, data: page([humanFriend]) });

    const { result } = renderHook(() => useBotIdentityFriendDirectory('bot-a:327325', true));
    await waitFor(() => expect(result.current.humanError).toBe('好友用户加载失败'));
    expect(result.current.botFriends).toEqual([botFriend]);

    act(() => result.current.reloadHuman());
    await waitFor(() => expect(result.current.humanFriends).toEqual([humanFriend]));

    expect(service.loadSection).toHaveBeenCalledWith('bot-a:327325', 'human', expect.any(AbortSignal));
    expect(service.loadSection).not.toHaveBeenCalledWith('bot-a:327325', 'bot', expect.anything());
  });
});
