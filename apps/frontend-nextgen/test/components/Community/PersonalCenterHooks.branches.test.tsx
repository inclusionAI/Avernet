/** @jest-environment jsdom */
import { useFollowedAuthorTopics } from '@/components/Community/CommunityPersonalCenter/useFollowedAuthorTopics';
import { useResolvedBackendBotNames } from '@/components/Community/CommunityPersonalCenter/useResolvedBackendBotNames';
import { actorLabel, personalCenterActivityOptions } from '@/components/Community/CommunityPersonalCenter/model';
import type { CommunityTopic, PersonalCenterActivity } from '@/domain/community/types';
import { communityService } from '@/services/community';
import { resolveBackendBotNames } from '@/services/community/botDisplayNames';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/community/botDisplayNames', () => ({
  resolveBackendBotNames: jest.fn(),
}));
jest.mock('@/services/community', () => ({
  communityService: {
    listTopics: jest.fn(),
    getInteractionSummaryBatch: jest.fn(),
  },
}));

function renderTopicsHook(item: PersonalCenterActivity | undefined) {
  return renderHook(
    ({ item: initialItem }: { item: PersonalCenterActivity | undefined }) =>
      useFollowedAuthorTopics(initialItem),
    { initialProps: { item } },
  );
}

type TopicPage = { items: CommunityTopic[]; total: number };

const selected: PersonalCenterActivity = {
  activityType: 'FOLLOWED',
  actor: { actorType: 'HUMAN', id: 'user-a' },
  target: { targetType: 'HUMAN', id: 'user-a' },
  happenedAt: '2026-10-01T08:00:00Z',
  bodyPreview: '用户 A',
};

const topic: CommunityTopic = {
  id: 'topic-1',
  title: '主题',
  body: '正文',
  status: 'open',
  topicType: 'discussion',
  author: { type: 'human', id: 'user-a', displayName: '用户 A' },
  createdAt: '2026-10-01T07:00:00Z',
  isMine: false,
  canClose: false,
  canEdit: false,
  canDelete: false,
};

const summary = {
  scope: 'TARGET' as const,
  target: { targetType: 'TOPIC' as const, id: 'topic-1' },
  viewCount: 1,
  likeCount: 2,
  favoriteCount: 3,
  followCount: 0,
  acceptCount: 0,
  topicCount: 1,
  replyCount: 0,
  acceptedReplyCount: 0,
  replyParticipantCount: 0,
  helpfulReplyCount: 0,
};

describe('PersonalCenter hooks', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.mocked(resolveBackendBotNames).mockResolvedValue({});
    jest.mocked(communityService.listTopics).mockResolvedValue({ items: [], total: 0 });
    jest.mocked(communityService.getInteractionSummaryBatch).mockResolvedValue([summary]);
  });

  it('loads a selected author topic page and maps topic summaries', async () => {
    jest.mocked(communityService.listTopics).mockResolvedValue({
      items: [topic],
      total: 1,
    });
    jest.mocked(communityService.getInteractionSummaryBatch).mockResolvedValue([
      summary,
      { ...summary, target: { targetType: 'POST', id: 'post-1' } },
    ]);

    const { result } = renderHook(({ item }) => useFollowedAuthorTopics(item), { initialProps: { item: selected } });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.topics).toEqual([topic]);
    expect(result.current.summariesById).toEqual({
      'topic-1': {
        scope: 'TARGET',
        target: { targetType: 'TOPIC', id: 'topic-1' },
        viewCount: 1,
        likeCount: 2,
        favoriteCount: 3,
        followCount: 0,
        acceptCount: 0,
        topicCount: 1,
        replyCount: 0,
        acceptedReplyCount: 0,
        replyParticipantCount: 0,
        helpfulReplyCount: 0,
      },
    });
  });

  it('handles empty, failed topic, and failed summary requests', async () => {
    const { result: empty } = renderTopicsHook({ ...selected });
    await act(async () => {});
    expect(empty.current.loading).toBe(false);
    expect(empty.current.topics).toEqual([]);

    jest.mocked(communityService.listTopics).mockResolvedValueOnce({
      items: [topic],
      total: 1,
    });
    jest.mocked(communityService.getInteractionSummaryBatch).mockRejectedValueOnce(
      new Error('summary failed'),
    );
    const { result: summaryError } = renderTopicsHook(
      { ...selected, target: { targetType: 'BOT', id: 'bot-a' } },
  );
    await act(async () => {});
    expect(summaryError.current.loading).toBe(false);
    expect(summaryError.current.summariesById).toEqual({});

    jest.mocked(communityService.listTopics).mockRejectedValueOnce(
      new Error('topics failed'),
    );
    const { result: topicsError } = renderTopicsHook(
      { ...selected, target: { targetType: 'HUMAN', id: 'user-b' } },
  );
    await act(async () => {});
    expect(topicsError.current.loading).toBe(false);
    expect(topicsError.current.topics).toEqual([]);
  });

  it('clears state when the selected author is removed', async () => {
    const { result, rerender } = renderTopicsHook({ ...selected });
    await act(async () => {});
    act(() => rerender({ item: undefined }));
    await act(async () => {});
    expect(result.current.topics).toEqual([]);
    expect(result.current.summariesById).toEqual({});
  });

  it('ignores a stale successful topic load after the selection changes', async () => {
    const authorA: PersonalCenterActivity = { ...selected, target: { targetType: 'HUMAN', id: 'user-a' } };
    const authorB: PersonalCenterActivity = { ...selected, target: { targetType: 'HUMAN', id: 'user-b' } };
    let resolveStaleTopic: ((page: TopicPage) => void) | undefined;
    jest.mocked(communityService.listTopics)
      .mockImplementationOnce(() => new Promise<TopicPage>((resolve) => { resolveStaleTopic = resolve; }))
      .mockImplementationOnce(() => new Promise<TopicPage>(() => {}));
    const hook = renderTopicsHook(authorA);
    act(() => hook.rerender({ item: authorB }));
    await act(async () => {
      resolveStaleTopic?.({ items: [topic], total: 1 });
    });
    expect(hook.result.current.topics).toEqual([]);
    expect(hook.result.current.loading).toBe(true);
  });

  it('ignores a stale rejected topic load after the selection changes', async () => {
    const authorA: PersonalCenterActivity = { ...selected, target: { targetType: 'HUMAN', id: 'user-a' } };
    const authorB: PersonalCenterActivity = { ...selected, target: { targetType: 'HUMAN', id: 'user-b' } };
    let rejectStaleTopic: ((reason?: unknown) => void) | undefined;
    jest.mocked(communityService.listTopics)
      .mockImplementationOnce(() => new Promise<TopicPage>((_, reject) => { rejectStaleTopic = reject; }))
      .mockImplementationOnce(() => new Promise<TopicPage>(() => {}));
    const hook = renderTopicsHook(authorA);
    act(() => hook.rerender({ item: authorB }));
    await act(async () => {
      rejectStaleTopic?.(new Error('stale topic load failed'));
    });
    expect(hook.result.current.topics).toEqual([]);
    expect(hook.result.current.loading).toBe(true);
  });

  it('resolves requested bot names', async () => {
    jest.mocked(resolveBackendBotNames).mockResolvedValueOnce({ 'bot-a': '已解析 Bot' });
    const { result } = renderHook(
      ({ ids }: { ids: string[] }) => useResolvedBackendBotNames(ids),
      { initialProps: { ids: ['bot-a'] } },
    );
    await waitFor(() =>
      expect(result.current).toEqual({ names: { 'bot-a': '已解析 Bot' }, loading: false }),
    );
  });

  it('skips resolving names when no bot ids are requested and exposes model labels', () => {
    const { result } = renderHook(() => useResolvedBackendBotNames([]));
    expect(result.current).toEqual({ names: {}, loading: false });
    expect(resolveBackendBotNames).not.toHaveBeenCalled();
    expect(actorLabel('BOT')).toBe('Bot');
    expect(actorLabel('HUMAN')).toBe('本人');
    expect(personalCenterActivityOptions).toHaveLength(6);
  });

})
