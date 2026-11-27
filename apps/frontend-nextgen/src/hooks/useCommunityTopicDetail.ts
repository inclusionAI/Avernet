import { COMMUNITY_REPLY_PAGE_SIZE } from '@/domain/community/constants';
import { communityErrorMessage } from '@/domain/community/errors';
import { enrichOwnership } from '@/domain/community/ownership';
import { buildTopicSummaryQueries } from '@/domain/community/interactionQueries';
import type {
  CommunityPostLocation,
  CommunityTopic,
  CommunityTopicOpenTarget,
  ForumActorRef,
  ForumTargetRef,
} from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCommunityStore } from '@/stores/communityStore';
import { useCallback, useRef, useState } from 'react';
import type {
  UseCommunityInteractionsResult,
} from './useCommunityInteractions';
import { useCommunityTopicActions } from './useCommunityTopicActions';
import { useCommunityReplies } from './useCommunityReplies';

interface UseCommunityTopicDetailOptions {
  currentUserId: string;
  currentAuthorName?: string;
  currentAuthorAvatarUrl?: string;
  viewer: ForumActorRef;
  interactions: UseCommunityInteractionsResult;
  enrichTopicBotAuthor: (topic: CommunityTopic) => CommunityTopic;
}

export function useCommunityTopicDetail({
  currentUserId,
  currentAuthorName,
  currentAuthorAvatarUrl,
  viewer,
  interactions,
  enrichTopicBotAuthor,
}: UseCommunityTopicDetailOptions) {
  const store = useCommunityStore();
  const replies = useCommunityReplies(store.setReplies);
  const { resetReplies, fetchReplyPage, findReplyPage, lastReplyPage, replyTotal } = replies;
  const detailGeneration = useRef(0);
  const [pendingPostLocation, setPendingPostLocation] =
    useState<CommunityPostLocation | null>(null);

  const fetchTopicWithViewer = useCallback(async (topicId: string, generation: number) => {
    const options = currentUserId
      ? { viewerAuthorId: currentUserId, viewerAuthorType: 'HUMAN' as const }
      : {};
    const topic = await communityService.getTopic(topicId, options);
    if (detailGeneration.current !== generation) return null;
    const { selectedTopic, topics } = useCommunityStore.getState();
    const cached = selectedTopic?.id === topicId ? selectedTopic : topics.find((item) => item.id === topicId);
    store.setSelectedTopic(
      enrichOwnership(enrichTopicBotAuthor({ ...topic, replyCount: topic.replyCount ?? cached?.replyCount }), currentUserId),
    );
    return topic;
  }, [currentUserId, enrichTopicBotAuthor, store.setSelectedTopic]);

  const loadTopicAndReplies = useCallback(
    async (topicId: string, generation: number, postId?: string) => {
      const loadedTopic = await fetchTopicWithViewer(topicId, generation);
      if (!loadedTopic) return;
      let page = 1;
      let location: CommunityPostLocation | null = null;
      if (postId) {
        location = await communityService.getPostLocation(
          topicId,
          postId,
          COMMUNITY_REPLY_PAGE_SIZE,
        );
        page = location.page;
      }
      if (detailGeneration.current !== generation) return;
      const replyPage = await fetchReplyPage(topicId, page);
      if (detailGeneration.current !== generation) return;
      setPendingPostLocation(location);
      const target: ForumTargetRef = { targetType: 'TOPIC', id: topicId };
      if (currentUserId) {
        await interactions.recordInteraction('VIEW', target, { refresh: false });
      }
      await interactions.refreshSummaries(
        buildTopicSummaryQueries({
          topic: loadedTopic,
          replies: replyPage?.items ?? [],
          viewer,
        }),
        { force: true },
      );
    },
    [
      currentUserId,
      fetchTopicWithViewer,
      interactions,
      fetchReplyPage,
      viewer,
    ],
  );

  const openTopic = useCallback(
    async (topic: CommunityTopic) => {
      const generation = ++detailGeneration.current;
      const owned = enrichOwnership(topic, currentUserId);
      store.setSelectedTopic(owned);
      store.setDetailLoading(true);
      store.setDetailError(null);
      resetReplies();
      setPendingPostLocation(null);
      try {
        await loadTopicAndReplies(topic.id, generation);
      } catch (error) {
        if (detailGeneration.current === generation) store.setDetailError(communityErrorMessage(error, '主题详情加载失败'));
      } finally {
        if (detailGeneration.current === generation) store.setDetailLoading(false);
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

  const openTopicById = useCallback(
    async (target: string | CommunityTopicOpenTarget) => {
      const generation = ++detailGeneration.current;
      const nextTarget =
        typeof target === 'string' ? { topicId: target } : target;
      store.setDetailLoading(true);
      store.setDetailError(null);
      resetReplies();
      setPendingPostLocation(null);
      try {
        await loadTopicAndReplies(nextTarget.topicId, generation, nextTarget.postId);
      } catch (error) {
        if (detailGeneration.current === generation) store.setDetailError(communityErrorMessage(error, '主题详情加载失败'));
      } finally {
        if (detailGeneration.current === generation) store.setDetailLoading(false);
      }
    },
    [
      loadTopicAndReplies,
      resetReplies,
      store.setDetailError,
      store.setDetailLoading,
    ],
  );

  const clearPendingPostLocation = useCallback(() => {
    setPendingPostLocation(null);
  }, []);

  const goToReplyPage = useCallback(
    async (page: number) => {
      const selected = store.selectedTopic;
      if (!selected) return;
      const replyPageResult = await fetchReplyPage(selected.id, page);
      if (!replyPageResult) return;
      await interactions.refreshSummaries(
        buildTopicSummaryQueries({
          topic: selected,
          replies: replyPageResult.items,
          viewer,
        }),
        { force: true },
      );
    },
    [fetchReplyPage, interactions, store.selectedTopic, viewer],
  );

  const refreshReply = useCallback(async (topicId: string, postId?: string) => {
    if (useCommunityStore.getState().selectedTopic?.id !== topicId) return;
    // Refresh the containing page for a nested reply or helpful mark. For a new
    // root, load at most one adjacent page so the rendered window has no gaps.
    const tail = Math.max(1, Math.ceil((replyTotal + 1) / COMMUNITY_REPLY_PAGE_SIZE));
    const page = postId ? findReplyPage(postId) ?? lastReplyPage : tail === lastReplyPage + 1 ? tail : lastReplyPage;
    await goToReplyPage(page);
  }, [findReplyPage, goToReplyPage, lastReplyPage, replyTotal]);

  const recordReplyCreated = useCallback((topicId: string) => {
    const current = useCommunityStore.getState();
    const observedCount = current.selectedTopic?.id === topicId
      ? replyTotal + current.replies.reduce((count, reply) => count + (reply.childReplies?.length ?? 0), 0)
      : 0;
    current.incrementTopicReplyCount(topicId, observedCount);
  }, [replyTotal]);

  const actions = useCommunityTopicActions({
    currentUserId,
    viewer,
    currentAuthorName,
    currentAuthorAvatarUrl,
    selectedTopic: store.selectedTopic,
    refreshReply,
    recordReplyCreated,
    interactions,
  });

  const closeDetail = useCallback(() => {
    detailGeneration.current += 1;
    resetReplies();
    setPendingPostLocation(null);
    store.setDetailLoading(false);
    store.setSelectedTopic(null);
  }, [resetReplies, store.setDetailLoading, store.setSelectedTopic]);

  return {
    ...replies,
    openTopic,
    openTopicById,
    pendingPostLocation,
    clearPendingPostLocation,
    ...actions,
    closeDetail,
    goToReplyPage,
  };
}
