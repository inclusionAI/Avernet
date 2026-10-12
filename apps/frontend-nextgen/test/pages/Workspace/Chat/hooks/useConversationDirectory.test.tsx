/** @jest-environment jsdom */
import { useConversationDirectory } from '@/pages/Workspace/Chat/hooks/useConversationDirectory';
import { botSessionService } from '@/services/workspace/botSessionService';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { teamBotConversationService } from '@/services/workspace/teamBotConversationService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botSessionService', () => ({
  botSessionService: { listOwnedBotsWithMeta: require('jest-mock').fn() },
  splitBotId: (botId: string) => {
    const idx = botId.indexOf(':');
    return idx < 0
      ? { realBotId: botId, ownerId: undefined }
      : { realBotId: botId.slice(0, idx), ownerId: botId.slice(idx + 1) };
  },
  BOT_SESSION_PAGE_SIZE: 10,
}));

jest.mock('@/services/workspace/collaborationCandidateService', () => ({
  collaborationCandidateService: { listFriends: require('jest-mock').fn() },
}));

jest.mock('@/services/workspace/teamBotConversationService', () => ({
  teamBotConversationService: { listBots: jest.fn() },
}));
const listTeam = jest.mocked(teamBotConversationService.listBots);

const listOwnedBotsWithMeta = botSessionService.listOwnedBotsWithMeta as unknown as jest.Mock;
const listFriends = collaborationCandidateService.listFriends as unknown as jest.Mock;

const ownedBot = {
  botId: 'bot-a',
  realBotId: 'bot-a',
  displayName: '管理 Bot',
  online: true,
  chatable: true,
};
const friendSource = {
  id: 'friend-bot:777',
  name: '皮皮虾',
  online: true,
  status: 'online' as const,
  reachability: 'reachable' as const,
  visibility: 'private' as const,
  detailsResolved: true,
};
const okOwned = { ok: true as const, data: { bots: [ownedBot], hasAgentCodingBots: true } };
const okFriends = {
  ok: true as const,
  data: { items: [friendSource], total: 1, offset: 0, limit: 100, hasMore: false },
};
const friendFailure = {
  code: 'COLLABORATION_FRIENDS_LOAD_FAILED',
  friendlyMessage: '加载好友 Bot 失败，请稍后重试。',
  canRetry: true,
};

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
};

beforeEach(() => {
  // require('jest-mock').fn() 的 mock 不归全局 clearAllMocks 管,逐个 reset。
  listTeam.mockReset().mockResolvedValue({ ok: true, data: [] });
  listOwnedBotsWithMeta.mockReset();
  listFriends.mockReset();
  useWorkspaceStore.setState({ activeIdentityId: 'bot-someone-else' });
});

describe('useConversationDirectory', () => {
  it('does not use workspace activeIdentityId', async () => {
    listOwnedBotsWithMeta.mockResolvedValue(okOwned);
    listFriends.mockResolvedValue(okFriends);

    renderHook(() => useConversationDirectory('327325'));

    await waitFor(() => expect(listOwnedBotsWithMeta).toHaveBeenCalledWith('327325'));
    expect(listOwnedBotsWithMeta).not.toHaveBeenCalledWith('bot-a:327325');
    expect(listFriends).toHaveBeenCalledWith('327325', { actorType: 'human', offset: 0, limit: 100 });
  });

  it('maps all managed sections including AgentCoding Bots and friend sections', async () => {
    listOwnedBotsWithMeta.mockResolvedValue(okOwned);
    listFriends.mockResolvedValue(okFriends);

    const { result } = renderHook(() => useConversationDirectory('327325'));
    expect(result.current.managedLoading).toBe(true);

    await waitFor(() => expect(result.current.managedLoading).toBe(false));
    expect(result.current.managedBots).toEqual([{ bot: ownedBot, section: 'managed' }]);
    expect(result.current.friendBots[0]).toMatchObject({ bot: { botId: 'friend-bot:777' }, section: 'friend' });
    expect(result.current.managedError).toBeNull();
    expect(result.current.friendError).toBeNull();
  });

  it('surfaces managed failure and retries the directory', async () => {
    listOwnedBotsWithMeta
      .mockResolvedValueOnce({
        ok: false,
        error: { code: 'X', friendlyMessage: '目录加载失败，请稍后重试。', canRetry: true },
      })
      .mockResolvedValue(okOwned);
    listFriends.mockResolvedValue(okFriends);

    const { result } = renderHook(() => useConversationDirectory('327325'));
    await waitFor(() => expect(result.current.managedError).toBe('目录加载失败，请稍后重试。'));
    expect(result.current.managedBots).toEqual([]);

    act(() => result.current.retryManaged());
    await waitFor(() => expect(result.current.managedBots).toHaveLength(1));
    expect(result.current.managedError).toBeNull();
    expect(listOwnedBotsWithMeta).toHaveBeenCalledTimes(2);
    expect(listFriends).toHaveBeenCalledTimes(2);
  });

  it('keeps the friend-list degrade silent (service degrades friend failure to empty)', async () => {
    listOwnedBotsWithMeta.mockResolvedValue(okOwned);
    listFriends.mockResolvedValue({ ok: false, error: friendFailure });

    const { result } = renderHook(() => useConversationDirectory('327325'));
    await waitFor(() => expect(result.current.managedBots).toHaveLength(1));

    expect(result.current.friendBots).toEqual([]);
    expect(result.current.friendError).toBeNull();
  });

  it('retries the combined directory when the friend section is retried', async () => {
    listOwnedBotsWithMeta.mockResolvedValue(okOwned);
    listFriends.mockResolvedValueOnce({ ok: false, error: friendFailure }).mockResolvedValue(okFriends);

    const { result } = renderHook(() => useConversationDirectory('327325'));
    await waitFor(() => expect(result.current.managedBots).toHaveLength(1));
    expect(result.current.friendBots).toEqual([]);

    act(() => result.current.retryFriend());
    await waitFor(() => expect(result.current.friendBots[0]?.bot.botId).toBe('friend-bot:777'));
    expect(listFriends).toHaveBeenCalledTimes(2);
  });

  it('ignores the in-flight response of the previous user', async () => {
    const first = deferred<Awaited<ReturnType<typeof listOwnedBotsWithMeta>>>();
    listOwnedBotsWithMeta
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue({ ok: true, data: { bots: [{ ...ownedBot, botId: 'bot-b' }], hasAgentCodingBots: false } });
    listFriends.mockResolvedValue({ ok: true, data: { items: [], total: 0, offset: 0, limit: 100, hasMore: false } });

    const { result, rerender } = renderHook(({ userId }) => useConversationDirectory(userId), {
      initialProps: { userId: '111' as string | null },
    });
    rerender({ userId: '222' });

    const view = await waitFor(() => {
      const current = result.current;
      expect(current.managedLoading).toBe(false);
      return current;
    });
    expect(view.managedBots[0]?.bot.botId).toBe('bot-b');

    await act(async () => {
      first.resolve(okOwned);
      await first.promise;
    });
    expect(result.current.managedBots[0]?.bot.botId).toBe('bot-b');
  });

  it('does not fetch without a user id', () => {
    const { result } = renderHook(() => useConversationDirectory(null));

    expect(listOwnedBotsWithMeta).not.toHaveBeenCalled();
    expect(listFriends).not.toHaveBeenCalled();
    expect(result.current.managedLoading).toBe(false);
    expect(result.current.friendLoading).toBe(false);
    expect(result.current.managedError).toBeNull();
    expect(result.current.friendError).toBeNull();
  });
});

it('团队加载独立：失败不影响管理/好友，可单独重试且按 canonical id 去重', async () => {
  listOwnedBotsWithMeta.mockResolvedValue(okOwned);
  listFriends.mockResolvedValue(okFriends);
  const teamBot = { ...ownedBot, botId: 'shared:team', realBotId: 'shared', ownerId: 'team' };
  listTeam
    .mockResolvedValueOnce({ ok: false, error: { code: 'FAILED', friendlyMessage: '团队失败', canRetry: true } })
    .mockResolvedValue({ ok: true, data: [ownedBot, teamBot] });
  const { result } = renderHook(() => useConversationDirectory('327325'));
  await waitFor(() => expect(result.current.teamError).toBe('团队失败'));
  expect(result.current.managedBots).toHaveLength(1);
  expect(result.current.friendBots).toHaveLength(1);
  act(() => result.current.retryTeam());
  await waitFor(() => expect(result.current.teamBots).toEqual([{ section: 'team', bot: teamBot }]));
  expect(listOwnedBotsWithMeta).toHaveBeenCalledTimes(1);
});
