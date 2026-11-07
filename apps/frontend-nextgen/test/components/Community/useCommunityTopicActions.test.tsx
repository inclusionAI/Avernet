/** @jest-environment jsdom */
import { useCommunityTopicActions } from '@/hooks/useCommunityTopicActions';
import type { CommunityAuthor } from '@/domain/community/types';
import type { UseCommunityInteractionsResult } from '@/hooks/useCommunityInteractions';
import { renderHook } from '@testing-library/react';

const interactions = {
  getSummary: jest.fn(),
  refreshSummaries: jest.fn().mockResolvedValue(undefined),
  recordInteraction: jest.fn(),
  isActionPending: jest.fn(),
} as unknown as UseCommunityInteractionsResult;

describe('useCommunityTopicActions', () => {
  it('加载作者资料时同时加载当前用户对作者的关注状态', () => {
    const author: CommunityAuthor = {
      type: 'bot',
      id: 'bot-1',
      displayName: '协作助手',
    };
    const viewer = { actorType: 'HUMAN' as const, id: 'human-1' };

    const { result } = renderHook(() => useCommunityTopicActions({
      currentUserId: viewer.id,
      viewer,
      selectedTopic: null,
      replyPage: 1,
      replyTotal: 0,
      goToReplyPage: jest.fn().mockResolvedValue(undefined),
      interactions,
    }));

    result.current.loadAuthorSummary(author);

    expect(interactions.refreshSummaries).toHaveBeenCalledWith([
      { actor: { actorType: 'BOT', id: 'bot-1' } },
      { actor: viewer, target: { targetType: 'BOT', id: 'bot-1' } },
    ]);
  });
});
