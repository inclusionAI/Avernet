import { communityErrorMessage } from '@/domain/community/errors';
import { enrichOwnership } from '@/domain/community/ownership';
import { buildTopicSummaryQueries } from '@/domain/community/interactionQueries';
import type {
  CommunityTopic,
  ForumActorRef,
  ForumTargetRef,
} from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCommunityStore } from '@/stores/communityStore';
import { useCallback } from 'react';
import type {
  UseCommunityInteractionsResult,
} from './useCommunityInteractions';
import { useCommunityTopicActions } from './useCommunityTopicActions';
import { useCommunityReplies } from './useCommunityReplies';

interface UseCommunityTopicDetailOptions {
  currentUserId: string;
  currentAuthorName?: string;
  viewer: ForumActorRef;
  interactions: UseCommunityInteractionsResult;
  enrichTopicBotAuthor: (topic: CommunityTopic) => CommunityTopic;
}

export function useCommunityTopicDetail({
  currentUserId,
  currentAuthorName,
  viewer,
  interactions,
  enrichTopicBotAuthor,
}: UseCommunityTopicDetailOptions) {
  const store = useCommunityStore();
  const replies = useCommunityReplies({
    selectedTopic: store.selectedTopic,
    setReplies: store.setReplies,
  });
  const {
    resetReplies,
    openFirstReplyPage,
    goToReplyPage: baseGoToReplyPage,
    replyPage,
    replyTotal,
  } = replies;

  const fetchTopicWithViewer = useCallback(async (topicId: string) => {
    const options = currentUserId
      ? { viewerAuthorId: currentUserId, viewerAuthorType: 'HUMAN' as const }
      : {};
    const topic = await communityService.getTopic(topicId, options);
    store.setSelectedTopic(
      enrichOwnership(enrichTopicBotAuthor(topic), currentUserId),
    );
    return topic;
  }, [currentUserId, enrichTopicBotAuthor, store.setSelectedTopic]);

  const loadTopicAndReplies = useCallback(async (topicId: string) => {
    const loadedTopic = await fetchTopicWithViewer(topicId);
    const replyPage = await openFirstReplyPage(topicId);
    const target: ForumTargetRef = { targetType: 'TOPIC', id: topicId };
    if (currentUserId) {
      await interactions.recordInteraction('VIEW', target, { refresh: false });
    }
    await interactions.refreshSummaries(
      buildTopicSummaryQueries({
        topic: loadedTopic,
        replies: replyPage.items,
        viewer,
      }),
      { force: true },
    );
  }, [
    currentUserId,
    fetchTopicWithViewer,
    interactions,
    openFirstReplyPage,
    viewer,
  ]);

  const openTopic = useCallback(
    async (topic: CommunityTopic) => {
      const owned = enrichOwnership(topic, currentUserId);
      store.setSelectedTopic(owned);
      store.setDetailLoading(true);
      store.setDetailError(null);
      resetReplies();
      try {
        await loadTopicAndReplies(topic.id);
      } catch (error) {
        store.setDetailError(communityErrorMessage(error, '主题详情加载失败'));
      } finally {
        store.setDetailLoading(false);
      }
    },
    [
      currentUserId,
      loadTopicAndReplies,
      resetReplies,
      store.setDetailError,
      store.setDetailLoading,
      store.setSelectedTopic,
    ],
  );

  const openTopicById = useCallback(async (topicId: string) => {
    store.setDetailLoading(true);
    store.setDetailError(null);
    resetReplies();
    try {
      await loadTopicAndReplies(topicId);
    } catch (error) {
      store.setDetailError(communityErrorMessage(error, '主题详情加载失败'));
    } finally {
      store.setDetailLoading(false);
    }
  }, [
    loadTopicAndReplies,
    resetReplies,
    store.setDetailError,
    store.setDetailLoading,
  ]);

  const goToReplyPage = useCallback(
    async (page: number) => {
      const selected = store.selectedTopic;
      if (!selected) return;
      const replyPageResult = await baseGoToReplyPage(page);
      await interactions.refreshSummaries(
        buildTopicSummaryQueries({
          topic: selected,
          replies: replyPageResult.items,
          viewer,
        }),
        { force: true },
      );
    },
    [baseGoToReplyPage, interactions, store.selectedTopic, viewer],
  );

  const actions = useCommunityTopicActions({
    currentUserId,
    currentAuthorName,
    selectedTopic: store.selectedTopic,
    replyPage,
    replyTotal,
    goToReplyPage,
    interactions,
  });

  const closeDetail = useCallback(() => {
    store.setSelectedTopic(null);
  }, [store.setSelectedTopic]);

  return {
    ...replies,
    openTopic,
    openTopicById,
    ...actions,
    closeDetail,
    goToReplyPage,
  };
}
