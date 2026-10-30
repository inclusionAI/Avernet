import { notifyError, notifySuccess } from '@/components/ui/notify';
import { COMMUNITY_REPLY_PAGE_SIZE } from '@/domain/community/constants';
import { communityErrorMessage } from '@/domain/community/errors';
import { forumAuthorRef } from '@/domain/community/interactionQueries';
import type {
  CommunityAuthor,
  CommunityTopic,
  CreateCommunityReplyDraft,
  ForumActorRef,
  ForumTargetRef,
  InteractionActionType,
  InteractionActiveDelta,
  InteractionSummary,
  InteractionSummaryQuery,
} from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCallback, useState } from 'react';
import type { UseCommunityInteractionsResult } from './useCommunityInteractions';

interface UseCommunityTopicActionsOptions {
  currentUserId: string;
  viewer: ForumActorRef;
  currentAuthorName?: string;
  selectedTopic: CommunityTopic | null;
  replyPage: number;
  replyTotal: number;
  goToReplyPage: (page: number) => Promise<unknown>;
  interactions: UseCommunityInteractionsResult;
}

function actionCount(
  actionType: InteractionActionType,
  summary: InteractionSummary | undefined,
): number {
  if (!summary) return 0;
  if (actionType === 'LIKE') return summary.likeCount;
  if (actionType === 'FAVORITE') return summary.favoriteCount;
  if (actionType === 'FOLLOW') return summary.followCount;
  return summary.acceptCount;
}

function replyTarget(postId: string): ForumTargetRef {
  return { targetType: 'POST', id: postId };
}

export function useCommunityTopicActions({
  currentUserId,
  viewer,
  currentAuthorName,
  selectedTopic,
  replyPage,
  replyTotal,
  goToReplyPage,
  interactions,
}: UseCommunityTopicActionsOptions) {
  const [helpfulPostId, setHelpfulPostId] = useState<string | null>(null);
  const [replySaving, setReplySaving] = useState(false);

  const createReply = useCallback(
    async (input: CreateCommunityReplyDraft) => {
      if (!selectedTopic || !currentUserId) return false;
      setReplySaving(true);
      try {
        await communityService.createReply({
          ...input,
          topicId: selectedTopic.id,
          authorId: currentUserId,
          ...(currentAuthorName ? { authorName: currentAuthorName } : {}),
        });
        const nextPage = input.replyToPostId
          ? replyPage
          : Math.max(1, Math.ceil((replyTotal + 1) / COMMUNITY_REPLY_PAGE_SIZE));
        await goToReplyPage(nextPage);
        notifySuccess('回复发布成功');
        return true;
      } catch (error) {
        notifyError(communityErrorMessage(error, '回复发布失败'), { title: '回复失败' });
        return false;
      } finally {
        setReplySaving(false);
      }
    },
    [currentAuthorName, currentUserId, goToReplyPage, replyPage, replyTotal, selectedTopic],
  );

  const getAuthorSummary = useCallback(
    (author: CommunityAuthor): InteractionSummary | undefined =>
      interactions.getSummary({ actor: forumAuthorRef(author) }),
    [interactions],
  );

  const loadAuthorSummary = useCallback(
    (author: CommunityAuthor) => {
      const authorTarget: ForumTargetRef = {
        targetType: author.type === 'bot' ? 'BOT' : 'HUMAN',
        id: author.id,
      };
      const queries: InteractionSummaryQuery[] = [
        { actor: forumAuthorRef(author) },
        { actor: viewer, target: authorTarget },
      ];
      void interactions.refreshSummaries(queries);
    },
    [interactions, viewer],
  );

  const authorFollowPending = useCallback(
    (author: CommunityAuthor): boolean =>
      interactions.isActionPending('FOLLOW', {
        targetType: author.type === 'bot' ? 'BOT' : 'HUMAN',
        id: author.id,
      }),
    [interactions],
  );

  const actionActive = useCallback(
    (actionType: InteractionActionType, target: ForumTargetRef): boolean => {
      const summary = interactions.getSummary({ actor: viewer, target });
      return actionCount(actionType, summary) > 0;
    },
    [interactions, viewer],
  );

  const toggleInteraction = useCallback(
    (actionType: InteractionActionType, target: ForumTargetRef) => {
      const active: InteractionActiveDelta = actionActive(actionType, target) ? -1 : 1;
      return interactions.recordInteraction(actionType, target, { active });
    },
    [actionActive, interactions],
  );

  const isAuthorFollowed = useCallback(
    (author: CommunityAuthor): boolean =>
      actionActive('FOLLOW', {
        targetType: author.type === 'bot' ? 'BOT' : 'HUMAN',
        id: author.id,
      }),
    [actionActive],
  );

  const followAuthor = useCallback(
    async (author: CommunityAuthor) => {
      const success = await toggleInteraction('FOLLOW', {
        targetType: author.type === 'bot' ? 'BOT' : 'HUMAN',
        id: author.id,
      });
      if (success) {
        await interactions.refreshSummaries([{ actor: forumAuthorRef(author) }], { force: true });
      }
      return success;
    },
    [interactions, toggleInteraction],
  );

  const likeTopic = useCallback(
    (target: ForumTargetRef) => toggleInteraction('LIKE', target),
    [toggleInteraction],
  );

  const likeReply = useCallback(
    (postId: string) => toggleInteraction('LIKE', replyTarget(postId)),
    [toggleInteraction],
  );

  const favoriteReply = useCallback(
    (postId: string) => toggleInteraction('FAVORITE', replyTarget(postId)),
    [toggleInteraction],
  );

  const favoriteTopic = useCallback(
    (target: ForumTargetRef) => toggleInteraction('FAVORITE', target),
    [toggleInteraction],
  );

  const targetSummary = useCallback(
    (topic: CommunityTopic | null): InteractionSummary | undefined =>
      topic ? interactions.getSummary({ target: { targetType: 'TOPIC', id: topic.id } }) : undefined,
    [interactions],
  );

  const setPostHelpful = useCallback(
    async (postId: string, helpful: boolean) => {
      if (!selectedTopic?.isMine || selectedTopic.topicType !== 'help') return false;
      setHelpfulPostId(postId);
      try {
        await communityService.setPostHelpful(
          selectedTopic.id,
          postId,
          selectedTopic.author.type === 'bot' ? 'BOT' : 'HUMAN',
          selectedTopic.author.id,
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
    [goToReplyPage, replyPage, selectedTopic],
  );

  return {
    createReply,
    replySaving,
    setPostHelpful,
    helpfulPostId,
    getAuthorSummary,
    loadAuthorSummary,
    authorFollowPending,
    isAuthorFollowed,
    followAuthor,
    likeTopic,
    likeReply,
    favoriteReply,
    favoriteTopic,
    targetSummary,
  };
}
