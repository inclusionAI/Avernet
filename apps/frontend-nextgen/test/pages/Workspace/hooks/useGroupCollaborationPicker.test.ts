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

function recommendResult(
  items: Array<{ id: string; name: string; recommendScore: number; shortProfile?: string }>,
) {
  return {
    ok: true,
    data: items.map((item) => ({
      online: true,
      status: 'online',
      reachability: 'reachable',
      visibility: 'public',
      ...item,
    })),
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

it('runs profile-mode recommendation with the loaded candidates as intersection input', async () => {
  cs.listFriends.mockResolvedValue(page([], 0, 0, false));
  cs.listCandidates.mockResolvedValue(page([{ id: 'b1', name: 'Alpha' }], 1, 0, false));
  cs.recommendCandidates.mockResolvedValue(
    recommendResult([{ id: 'b1', name: 'Alpha', recommendScore: 0.9, shortProfile: '值班相关' }]),
  );

  const { result } = renderHook(() => useGroupCollaborationPicker('actor-1', true, false, 'bot'));
  act(() => result.current.setTab('candidates'));
  act(() => result.current.setFilterMode('profile'));
  act(() => result.current.setSearch('值班'));
  await waitFor(() => expect(result.current.recommendations).toHaveLength(1));

  expect(cs.recommendCandidates).toHaveBeenCalledTimes(1);
  expect(cs.recommendCandidates).toHaveBeenCalledWith(
    expect.objectContaining({
      actorId: 'actor-1',
      actorKind: 'bot',
      keyword: '值班',
      candidates: [expect.objectContaining({ id: 'b1', name: 'Alpha' })],
    }),
  );
  expect(result.current.recommendations[0]).toMatchObject({
    id: 'b1',
    recommendScore: 0.9,
    shortProfile: '值班相关',
  });
  // 原可协作列表不被推荐结果覆盖。
  expect(result.current.candidates).toEqual([expect.objectContaining({ id: 'b1', name: 'Alpha' })]);
  expect(result.current.isLoadingRecommend).toBe(false);
  expect(result.current.recommendError).toBeUndefined();
});

it('clears recommendations when the profile keyword is emptied', async () => {
  cs.listFriends.mockResolvedValue(page([], 0, 0, false));
  cs.listCandidates.mockResolvedValue(page([{ id: 'b1', name: 'Alpha' }], 1, 0, false));
  cs.recommendCandidates.mockResolvedValue(
    recommendResult([{ id: 'b1', name: 'Alpha', recommendScore: 0.9 }]),
  );

  const { result } = renderHook(() => useGroupCollaborationPicker('actor-1', true, false, 'bot'));
  act(() => result.current.setTab('candidates'));
  act(() => result.current.setFilterMode('profile'));
  act(() => result.current.setSearch('值班'));
  await waitFor(() => expect(result.current.recommendations).toHaveLength(1));

  act(() => result.current.setSearch(''));
  await waitFor(() => expect(result.current.recommendations).toHaveLength(0));
  expect(cs.recommendCandidates).toHaveBeenCalledTimes(1);
});

it('keeps name mode on listCandidates and reuses the keyword after switching back from profile', async () => {
  cs.listFriends.mockResolvedValue(page([], 0, 0, false));
  cs.listCandidates.mockReset();
  cs.listCandidates.mockResolvedValue(page([{ id: 'b3', name: 'Gamma' }], 1, 0, false));
  cs.recommendCandidates.mockResolvedValue(
    recommendResult([{ id: 'b3', name: 'Gamma', recommendScore: 0.5 }]),
  );

  const { result } = renderHook(() => useGroupCollaborationPicker('actor-1', true, false, 'bot'));
  act(() => result.current.setTab('candidates'));
  await waitFor(() => expect(result.current.candidates).toHaveLength(1));
  expect(result.current.filterMode).toBe('name');

  act(() => result.current.setFilterMode('profile'));
  act(() => result.current.setSearch('值班'));
  await waitFor(() => expect(result.current.recommendations).toHaveLength(1));
  expect(cs.recommendCandidates).toHaveBeenCalledTimes(1);

  cs.listCandidates.mockClear();
  act(() => result.current.setFilterMode('name'));
  await waitFor(() => expect(cs.listCandidates).toHaveBeenCalledWith('actor-1', {
    name: '值班',
    offset: 0,
    limit: 50,
  }));
  expect(result.current.recommendations).toHaveLength(0);
});

it('recommendByGoal switches to the candidates tab in profile mode and triggers recommendation', async () => {
  cs.listFriends.mockResolvedValue(page([], 0, 0, false));
  cs.listCandidates.mockResolvedValue(page([{ id: 'b2', name: 'Beta' }], 1, 0, false));
  cs.recommendCandidates.mockResolvedValue(
    recommendResult([{ id: 'b2', name: 'Beta', recommendScore: 0.4, shortProfile: '分析相关' }]),
  );

  const { result } = renderHook(() => useGroupCollaborationPicker('human_1', true, true, 'user'));
  act(() => result.current.recommendByGoal('优化 Feature 开发效率'));
  await waitFor(() => expect(result.current.recommendations).toHaveLength(1));

  expect(result.current.tab).toBe('candidates');
  expect(result.current.filterMode).toBe('profile');
  expect(result.current.search).toBe('优化 Feature 开发效率');
  expect(result.current.recommendSource).toBe('goal');
  expect(cs.recommendCandidates).toHaveBeenCalledWith(
    expect.objectContaining({ actorId: 'human_1', actorKind: 'user', keyword: '优化 Feature 开发效率' }),
  );
});

it('marks manual search edits as profile source after a goal recommendation', async () => {
  cs.listFriends.mockResolvedValue(page([], 0, 0, false));
  cs.listCandidates.mockResolvedValue(page([{ id: 'b2', name: 'Beta' }], 1, 0, false));
  cs.recommendCandidates.mockResolvedValue(
    recommendResult([{ id: 'b2', name: 'Beta', recommendScore: 0.4, shortProfile: '分析相关' }]),
  );

  const { result } = renderHook(() => useGroupCollaborationPicker('human_1', true, true, 'user'));
  act(() => result.current.recommendByGoal('优化 Feature 开发效率'));
  await waitFor(() => expect(result.current.recommendations).toHaveLength(1));
  expect(result.current.recommendSource).toBe('goal');

  act(() => result.current.setSearch('值班'));
  expect(result.current.recommendSource).toBe('profile');
});

it('exposes recommend error without breaking the original list', async () => {
  cs.listFriends.mockResolvedValue(page([], 0, 0, false));
  cs.listCandidates.mockResolvedValue(page([{ id: 'b1', name: 'Alpha' }], 1, 0, false));
  cs.recommendCandidates.mockResolvedValue({
    ok: false,
    error: { code: 'COLLABORATION_RECOMMEND_FAILED', friendlyMessage: '推荐可协作 Bot 失败，请稍后重试。', canRetry: false },
  });

  const { result } = renderHook(() => useGroupCollaborationPicker('actor-1', true, false, 'bot'));
  act(() => result.current.setTab('candidates'));
  act(() => result.current.setFilterMode('profile'));
  act(() => result.current.setSearch('值班'));
  await waitFor(() => expect(result.current.recommendError).toBe('推荐可协作 Bot 失败，请稍后重试。'));

  expect(result.current.recommendations).toHaveLength(0);
  expect(result.current.candidates).toEqual([expect.objectContaining({ id: 'b1', name: 'Alpha' })]);
});

it('resets filter mode and recommendations after the dialog closes and reopens', async () => {
  cs.listFriends.mockResolvedValue(page([], 0, 0, false));
  cs.listCandidates.mockResolvedValue(page([{ id: 'b1', name: 'Alpha' }], 1, 0, false));
  cs.recommendCandidates.mockResolvedValue(
    recommendResult([{ id: 'b1', name: 'Alpha', recommendScore: 0.9 }]),
  );

  const { result, rerender } = renderHook(({ enabled }: { enabled: boolean }) =>
    useGroupCollaborationPicker('actor-1', enabled, false, 'bot'),
    { initialProps: { enabled: true } },
  );
  act(() => result.current.setTab('candidates'));
  act(() => result.current.setFilterMode('profile'));
  act(() => result.current.setSearch('值班'));
  await waitFor(() => expect(result.current.recommendations).toHaveLength(1));

  act(() => rerender({ enabled: false }));
  await waitFor(() => {
    expect(result.current.filterMode).toBe('name');
    expect(result.current.search).toBe('');
    expect(result.current.tab).toBe('friends');
  });
  expect(result.current.recommendations).toHaveLength(0);
  expect(result.current.recommendError).toBeUndefined();
  expect(result.current.recommendSource).toBeNull();
});
