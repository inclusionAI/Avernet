/** @jest-environment jsdom */
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { PERSONAL_CENTER_PAGE_SIZE } from '@/domain/community/constants';
import { usePersonalCenter } from '@/hooks/usePersonalCenter';
import { communityService } from '@/services/community';
import { act, renderHook, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));
jest.mock('@/services/community', () => ({
  communityService: {
    listBrowseSubscriptions: jest.fn(),
    getInteractionSummaryBatch: jest.fn(),
    getPersonalCenterActivities: jest.fn(),
  },
}));

const mockedIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;
const mockedSummaryBatch = communityService.getInteractionSummaryBatch as unknown as jest.Mock;
const batchSummary = {
  scope: 'ACTOR' as const,
  followCount: 0,
  viewCount: 0,
  likeCount: 0,
  favoriteCount: 0,
  acceptCount: 0,
  topicCount: 0,
  replyCount: 0,
  acceptedReplyCount: 0,
  replyParticipantCount: 0,
  helpfulReplyCount: 0,
};

describe('usePersonalCenter', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedIdentity.mockReturnValue({
      identity: { userId: '149844', displayName: '当前用户', online: true },
      status: 'ready',
    });
    jest.mocked(communityService.listBrowseSubscriptions).mockResolvedValue(['bot-A']);
    jest.mocked(communityService.getInteractionSummaryBatch).mockResolvedValue([batchSummary, batchSummary]);
    jest.mocked(communityService.getPersonalCenterActivities).mockResolvedValue({ items: [], total: 0 });
  });

  it('uses ALL as the default scope, reads subscription bots, then issues one batch summary', async () => {
    const { result } = renderHook(() => usePersonalCenter());
    expect(result.current.actorScope).toBe('ALL');
    await waitFor(() => expect(communityService.getInteractionSummaryBatch).toHaveBeenCalledTimes(1));
    expect(communityService.listBrowseSubscriptions).toHaveBeenCalledWith('149844');
    expect(communityService.getInteractionSummaryBatch).toHaveBeenCalledWith([
      { actor: { actorType: 'HUMAN', id: '149844' } },
      { actor: { actorType: 'BOT', id: 'bot-A' } },
    ]);
    expect(communityService.getPersonalCenterActivities).toHaveBeenCalledWith({
      ownerUserId: '149844',
      actorScope: 'ALL',
      activityType: 'AUTHORED',
      targetType: 'ALL',
      page: 1,
      pageSize: PERSONAL_CENTER_PAGE_SIZE,
    });
  });

  it('switching BOT scope resets to page one and derives only actors from subscriptions', async () => {
    const { result } = renderHook(() => usePersonalCenter());
    await waitFor(() => expect(communityService.getInteractionSummaryBatch).toHaveBeenCalledTimes(1));
    act(() => result.current.setActorScope('BOT'));
    await waitFor(() => {
      expect(mockedSummaryBatch.mock.calls.length).toBeGreaterThan(1);
    });
    expect(communityService.getInteractionSummaryBatch).toHaveBeenLastCalledWith([
      { actor: { actorType: 'BOT', id: 'bot-A' } },
    ]);
    expect(result.current.page).toBe(1);
  });

  it('retries summary statistics on reload and clears the stale error', async () => {
    mockedSummaryBatch
      .mockRejectedValueOnce(new Error('stats unavailable'))
      .mockResolvedValue([batchSummary, batchSummary]);
    const { result } = renderHook(() => usePersonalCenter());
    await waitFor(() => expect(result.current.error).not.toBeNull());
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.error).toBeNull());
    const lastActors = mockedSummaryBatch.mock.calls.at(-1)?.[0] ?? [];
    expect(lastActors).toEqual([
      { actor: { actorType: 'HUMAN', id: '149844' } },
      { actor: { actorType: 'BOT', id: 'bot-A' } },
    ]);
  });
});