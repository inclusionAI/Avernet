/** @jest-environment jsdom */
import { useGroupCollaborationPicker } from '@/pages/Workspace/hooks/useGroupCollaborationPicker';
import { collaborationCandidateService } from '@/services/workspace/collaborationCandidateService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/collaborationCandidateService');

const cs = collaborationCandidateService as unknown as Record<string, jest.Mock<any>>;

function page(items: Array<{ id: string; name: string }>, total: number, offset: number, hasMore: boolean) {
  return {
    ok: true,
    data: {
      items: items.map((item) => ({
        ...item,
        online: true,
        status: 'online',
        reachability: 'reachable',
        visibility: 'public',
      })),
      total,
      offset,
      limit: 50,
      hasMore,
    },
  };
}

beforeEach(() => {
  jest.resetAllMocks();
});

it('loads more friends with the next offset', async () => {
  cs.listFriends
    .mockResolvedValueOnce(page([{ id: 'b1', name: 'Alpha' }], 60, 0, true))
    .mockResolvedValueOnce(page([{ id: 'b2', name: 'Beta' }], 60, 50, false));

  const { result } = renderHook(() => useGroupCollaborationPicker('actor-1', true));
  await waitFor(() => expect(result.current.friends).toHaveLength(1));

  act(() => result.current.loadMore());
  await waitFor(() => expect(result.current.friends).toHaveLength(2));

  expect(cs.listFriends).toHaveBeenNthCalledWith(1, 'actor-1', {
    offset: 0,
    limit: 50,
    detailSource: 'collaboration',
  });
  expect(cs.listFriends).toHaveBeenNthCalledWith(2, 'actor-1', {
    offset: 50,
    limit: 50,
    detailSource: 'collaboration',
  });
  expect(result.current.friendsHasMore).toBe(false);
});

it('loads more candidates with the current search term', async () => {
  cs.listFriends.mockResolvedValue(page([], 0, 0, false));
  cs.listCandidates
    .mockResolvedValueOnce(page([{ id: 'b3', name: 'Gamma' }], 58, 0, true))
    .mockResolvedValueOnce(page([{ id: 'b4', name: 'Delta' }], 58, 50, false));

  const { result } = renderHook(() => useGroupCollaborationPicker('actor-1', true));
  act(() => result.current.setTab('candidates'));
  await waitFor(() => expect(result.current.candidates).toHaveLength(1));

  act(() => result.current.loadMore());
  await waitFor(() => expect(result.current.candidates).toHaveLength(2));

  expect(cs.listCandidates).toHaveBeenNthCalledWith(2, 'actor-1', {
    name: undefined,
    offset: 50,
    limit: 50,
  });
  expect(result.current.candidatesHasMore).toBe(false);
});

it('loads the user-owned bots tab when showMineTab is enabled', async () => {
  cs.listFriends.mockResolvedValue(page([], 0, 0, false));
  cs.listMine.mockResolvedValueOnce(page([{ id: 'mine-1', name: '我的 Bot' }], 1, 0, false));

  const { result } = renderHook(() => useGroupCollaborationPicker('actor-1', true, true));
  act(() => result.current.setTab('mine'));
  await waitFor(() => expect(result.current.mine).toHaveLength(1));

  expect(cs.listMine).toHaveBeenCalledWith({ offset: 0, limit: 50 });
  expect(result.current.mineHasMore).toBe(false);
});
