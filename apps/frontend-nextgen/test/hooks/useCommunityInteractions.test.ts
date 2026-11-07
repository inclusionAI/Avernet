/** @jest-environment jsdom */
import { MAX_INTERACTION_SUMMARY_BATCH } from '@/domain/community/constants';
import { useCommunityInteractions } from '@/hooks/useCommunityInteractions';
import { communityService } from '@/services/community';
import { act, renderHook, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('@/services/community', () => ({
  communityService: {
    createInteraction: jest.fn(),
    getInteractionSummaryBatch: jest.fn(),
  },
}));

const mockedService = communityService as jest.Mocked<typeof communityService>;

const summary = {
  scope: 'ACTOR' as const,
  followCount: 1,
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

const viewer = { actorType: 'HUMAN' as const, id: '149844' };

describe('useCommunityInteractions', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedService.createInteraction.mockResolvedValue(undefined);
    mockedService.getInteractionSummaryBatch.mockResolvedValue([summary]);
  });

  it('refreshes only the first 100 unique summary items in a page-sized batch', async () => {
    const { result } = renderHook(() => useCommunityInteractions({ viewer }));

    await act(async () => {
      await result.current.refreshSummaries(
        Array.from({ length: MAX_INTERACTION_SUMMARY_BATCH + 1 }, (_, index) => ({
          target: { targetType: 'TOPIC', id: `topic-${index}` },
        })),
      );
    });

    expect(mockedService.getInteractionSummaryBatch).toHaveBeenCalledTimes(2);
    const [firstBatch] = mockedService.getInteractionSummaryBatch.mock.calls;
    expect(firstBatch?.[0]).toHaveLength(MAX_INTERACTION_SUMMARY_BATCH);
  });

  it('writes an event without optimistic state and rereads the authoritative target summary', async () => {
    const { result } = renderHook(() => useCommunityInteractions({ viewer }));
    let succeeded = false;
    await act(async () => {
      succeeded = await result.current.recordInteraction('LIKE', {
        targetType: 'TOPIC',
        id: 'topic-1',
      });
    });
    await waitFor(() => expect(mockedService.getInteractionSummaryBatch).toHaveBeenCalledTimes(1));

    expect(succeeded).toBe(true);
    expect(mockedService.createInteraction).toHaveBeenCalledWith({
      actor: viewer,
      actionType: 'LIKE',
      target: { targetType: 'TOPIC', id: 'topic-1' },
      active: 1,
    });
    expect(result.current.getSummary({ target: { targetType: 'TOPIC', id: 'topic-1' } })).toEqual(
      expect.objectContaining({ followCount: 1 }),
    );
  });

  it('passes the requested -1 delta so canceling a Like writes active=-1', async () => {
    const { result } = renderHook(() => useCommunityInteractions({ viewer }));
    let succeeded = false;
    await act(async () => {
      succeeded = await result.current.recordInteraction(
        'LIKE',
        { targetType: 'TOPIC', id: 'topic-1' },
        { active: -1 },
      );
    });

    expect(succeeded).toBe(true);
    expect(mockedService.createInteraction).toHaveBeenCalledWith({
      actor: viewer,
      actionType: 'LIKE',
      target: { targetType: 'TOPIC', id: 'topic-1' },
      active: -1,
    });
  });

  it('keeps the button-like event pending only while the write and summary are in flight', async () => {
    let resolveWrite: (() => void) | undefined;
    mockedService.createInteraction.mockImplementation(
      () => new Promise<void>((resolve) => { resolveWrite = resolve; }),
    );
    const { result } = renderHook(() => useCommunityInteractions({ viewer }));
    expect(result.current.isActionPending('LIKE', { targetType: 'TOPIC', id: 'topic-1' })).toBe(false);

    let complete: Promise<boolean> | undefined;
    act(() => {
      complete = result.current.recordInteraction('LIKE', { targetType: 'TOPIC', id: 'topic-1' });
    });
    await act(async () => Promise.resolve());
    expect(result.current.isActionPending('LIKE', { targetType: 'TOPIC', id: 'topic-1' })).toBe(true);
    act(() => resolveWrite?.());
    await act(async () => { await complete; });
    expect(result.current.isActionPending('LIKE', { targetType: 'TOPIC', id: 'topic-1' })).toBe(false);
  });
});
