/** @jest-environment jsdom */
import { useResolvedBackendBotNames } from '@/components/Community/CommunityPersonalCenter/useResolvedBackendBotNames';
import { useFollowedAuthorTopics } from '@/components/Community/CommunityPersonalCenter/useFollowedAuthorTopics';
import type { CommunityTopic, PersonalCenterActivity } from '@/domain/community/types';
import { communityService } from '@/services/community';
import { resolveBackendBotNames } from '@/services/community/botDisplayNames';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/services/community/botDisplayNames', () => ({
  resolveBackendBotNames: jest.fn(),
}));
jest.mock('@/services/community', () => ({
  communityService: {
    getInteractionSummaryBatch: jest.fn(),
    listTopics: jest.fn(),
  },
}));

const followed: PersonalCenterActivity = {
  activityType: 'FOLLOWED',
  actor: { actorType: 'HUMAN', id: 'user-a' },
  target: { targetType: 'HUMAN', id: 'user-a' },
  happenedAt: '2026-10-01T08:00:00Z',
  bodyPreview: '作者 A',
};

const nextFollowed: PersonalCenterActivity = {
  ...followed,
  target: { targetType: 'HUMAN', id: 'user-b' },
};

const topic: CommunityTopic = {
  id: 'topic-1',
  title: '主题',
  body: '正文',
  status: 'open',
  topicType: 'discussion',
  author: { type: 'human', id: 'user-a', displayName: '作者 A' },
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

function renderTopicsHook(item: PersonalCenterActivity) {
  return renderHook(
    ({ item: initialItem }: { item: PersonalCenterActivity }) =>
      useFollowedAuthorTopics(initialItem),
    { initialProps: { item } },
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(resolveBackendBotNames).mockResolvedValue({});
  jest.mocked(communityService.listTopics).mockResolvedValue({ items: [], total: 0 });
  jest.mocked(communityService.getInteractionSummaryBatch).mockResolvedValue([]);
});

it('ignores a stale successful summary after the selected author changes', async () => {
  let resolveSummary: ((value: Awaited<ReturnType<typeof communityService.getInteractionSummaryBatch>>) => void) | undefined;
  jest.mocked(communityService.listTopics).mockResolvedValueOnce({ items: [topic], total: 1 });
  jest.mocked(communityService.getInteractionSummaryBatch).mockImplementationOnce(
    () => new Promise<Awaited<ReturnType<typeof communityService.getInteractionSummaryBatch>>>((resolve) => {
      resolveSummary = resolve;
    }),
  );
  const hook = renderTopicsHook(followed);
  await act(async () => {
    await new Promise((resolve) => { setTimeout(resolve, 0); });
  });
  expect(resolveSummary).toBeDefined();

  act(() => hook.rerender({ item: nextFollowed }));
  await act(async () => {
    resolveSummary?.([summary]);
  });
  expect(hook.result.current.summariesById).toEqual({});
});

it('ignores a stale rejected summary after the selected author changes', async () => {
  let rejectSummary: ((reason?: unknown) => void) | undefined;
  jest.mocked(communityService.listTopics).mockResolvedValueOnce({ items: [topic], total: 1 });
  jest.mocked(communityService.getInteractionSummaryBatch).mockImplementationOnce(
    () => new Promise<Awaited<ReturnType<typeof communityService.getInteractionSummaryBatch>>>((_, reject) => {
      rejectSummary = reject;
    }),
  );
  const hook = renderTopicsHook(followed);
  await act(async () => {
    await new Promise((resolve) => { setTimeout(resolve, 0); });
  });
  expect(rejectSummary).toBeDefined();

  act(() => hook.rerender({ item: nextFollowed }));
  await act(async () => {
    rejectSummary?.(new Error('stale summary failed'));
  });
  expect(hook.result.current.summariesById).toEqual({});
});

it('ignores a stale resolved bot-name request after the requested ids change', async () => {
  let resolveNames: ((value: Record<string, string>) => void) | undefined;
  jest.mocked(resolveBackendBotNames).mockImplementationOnce(
    () => new Promise<Record<string, string>>((resolve) => {
      resolveNames = resolve;
    }),
  );
  jest.mocked(resolveBackendBotNames).mockImplementation(
    () => new Promise<Record<string, string>>(() => {}),
  );
  const hook = renderHook(
    ({ ids }: { ids: string[] }) => useResolvedBackendBotNames(ids),
    { initialProps: { ids: ['bot-a'] } },
  );
  await act(async () => {
    await new Promise((resolve) => { setTimeout(resolve, 0); });
  });
  expect(resolveNames).toBeDefined();

  act(() => hook.rerender({ ids: ['bot-b'] }));
  await act(async () => {
    resolveNames?.({ 'bot-a': '陈旧名称' });
  });
  expect(hook.result.current).toEqual({ names: {}, loading: true });
});
