/** @jest-environment jsdom */
import { getCapabilities } from '@/capabilities';
import { useFriendBots } from '@/pages/Workspace/hooks/useFriendBots';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/capabilities');
jest.mock('@/services/workspace/collaborationCandidateService');

const mockedGetCurrentOpenApiUserId = getCapabilities as jest.Mock;
const mockedListFriends = collaborationCandidateService.listFriends as jest.Mock;

beforeEach(() => {
  jest.resetAllMocks();
  mockedGetCurrentOpenApiUserId.mockReturnValue({
    getCurrentOpenApiUserId: () => ({ status: 'available', value: '327325' }),
  });
  mockedListFriends.mockResolvedValue({
    ok: true,
    data: {
      items: [{ id: 'friend-bot:327325', name: '好友 Bot', online: true }],
      total: 1,
    },
  });
});

it('queries Human friends with the Human actor type', async () => {
  const { result } = renderHook(() => useFriendBots('human_327325', true, true));

  await act(async () => Promise.resolve());

  expect(mockedListFriends).toHaveBeenCalledWith('327325', {
    actorType: 'human',
    offset: 0,
    limit: 100,
  });
  expect(result.current.friendBots).toEqual([
    expect.objectContaining({ botId: 'friend-bot:327325', displayName: '好友 Bot' }),
  ]);
});

it('queries Bot friends with the full Bot uuid as actor_id', async () => {
  const { result } = renderHook(() => useFriendBots('friend-owner-bot:327325', false, true));

  await act(async () => Promise.resolve());

  expect(mockedListFriends).toHaveBeenCalledWith('friend-owner-bot:327325', {
    actorType: 'bot',
    offset: 0,
    limit: 100,
  });
  expect(result.current.friendBots).toEqual([
    expect.objectContaining({ botId: 'friend-bot:327325', displayName: '好友 Bot' }),
  ]);
});
