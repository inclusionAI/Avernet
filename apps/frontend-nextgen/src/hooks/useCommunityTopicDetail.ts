import { notifyError, notifySuccess } from '@/components/ui/notify';
import { communityErrorMessage } from '@/domain/community/errors';
import { enrichOwnership } from '@/domain/community/ownership';
import {
  buildTopicSummaryQueries,
  forumAuthorRef,
} from '@/domain/community/interactionQueries';
import type {
  CommunityAuthor,
  CommunityTopic,
  ForumActorRef,
  ForumTargetRef,
  InteractionSummary,
} from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCommunityStore } from '@/stores/communityStore';
import { useCallback, useState } from 'react';
import type {
  UseCommunityInteractionsResult,
} from './useCommunityInteractions';
import { useCommunityReplies } from './useCommunityReplies';

interface UseCommunityTopicDetailOptions {
  currentUserId: string;
  viewer: ForumActorRef;
  interactions: UseCommunityInteractionsResult;
  enrichTopicBotAuthor: (topic: CommunityTopic) => CommunityTopic;
}

export function useCommunityTopicDetail({
  currentUserId,
  viewer,
  interactions,
  enrichTopicBotAuthor,
}: UseCommunityTopicDetailOptions) {
  const store = useCommunityStore();
  const [helpfulPostId, setHelpfulPostId] = useState<string | null>(null);

  const replies = useCommunityReplies({
    selectedTopic: store.selectedTopic,
    setReplies: store.setReplies,
  });
  const {
    resetReplies,
    openFirstReplyPage,
    goToReplyPage: baseGoToReplyPage,
    replyPage,
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

  const getAuthorSummary = useCallback(
    (author: CommunityAuthor): InteractionSummary | undefined =>
      interactions.getSummary({ actor: forumAuthorRef(author) }),
    [interactions],
  );

  const loadAuthorSummary = useCallback((author: CommunityAuthor) => {
    void interactions.refreshSummaries([{ actor: forumAuthorRef(author) }]);
  }, [interactions]);

  const authorFollowPending = useCallback((author: CommunityAuthor): boolean =>
    interactions.isActionPending('FOLLOW', {
      targetType: author.type === 'bot' ? 'BOT' : 'HUMAN',
      id: author.id,
    }), [interactions]);

  const followAuthor = useCallback(
    async (author: CommunityAuthor) => {
      const success = await interactions.recordInteraction('FOLLOW', {
        targetType: author.type === 'bot' ? 'BOT' : 'HUMAN',
        id: author.id,
      });
      if (success) {
        await interactions.refreshSummaries(
          [{ actor: forumAuthorRef(author) }],
          { force: true },
        );
      }
      return success;
    },
    [interactions],
  );

  const likeTopic = useCallback(
    (target: ForumTargetRef) => interactions.recordInteraction('LIKE', target),
    [interactions],
  );

  const favoriteTopic = useCallback(
    (target: ForumTargetRef) => interactions.recordInteraction('FAVORITE', target),
    [interactions],
  );

  const targetSummary = useCallback(
    (topic: CommunityTopic | null): InteractionSummary | undefined =>
      topic
        ? interactions.getSummary({ target: { targetType: 'TOPIC', id: topic.id } })
        : undefined,
    [interactions],
  );

  const setPostHelpful = useCallback(
    async (postId: string, helpful: boolean) => {
      const topic = store.selectedTopic;
      if (!topic?.isMine || topic.topicType !== 'help') return false;
      setHelpfulPostId(postId);
      try {
        await communityService.setPostHelpful(
          topic.id,
          postId,
          topic.author.type === 'bot' ? 'BOT' : 'HUMAN',
          topic.author.id,
          helpful,
        );
        await goToReplyPage(replyPage);
        notifySuccess(helpful ? '已标记为有帮助' : '已取消有帮助');
        return true;
      } catch (error) {
        notifyError(communityErrorMessage(error, '有帮助状态更新失败'), {
          title: '操作失败',
        });
        return false;
      } finally {
        setHelpfulPostId(null);
      }
    },
    [goToReplyPage, replyPage, store.selectedTopic],
  );

  const closeDetail = useCallback(() => {
    store.setSelectedTopic(null);
  }, [store.setSelectedTopic]);

  return {
    ...replies,
    openTopic,
    openTopicById,
    closeDetail,
    setPostHelpful,
    helpfulPostId,
    getAuthorSummary,
    loadAuthorSummary,
    authorFollowPending,
    followAuthor,
    likeTopic,
    favoriteTopic,
    targetSummary,
    goToReplyPage,
  };
}
