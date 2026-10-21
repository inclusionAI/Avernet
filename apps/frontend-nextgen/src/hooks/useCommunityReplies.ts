import { notifyError, notifySuccess } from '@/components/ui/notify';
import { COMMUNITY_REPLY_PAGE_SIZE } from '@/domain/community/constants';
import { communityErrorMessage } from '@/domain/community/errors';
import type { CommunityReply, CommunityTopic } from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCallback, useState } from 'react';

interface UseCommunityRepliesOptions {
  currentUserId: string;
  refreshTopic: (topicId: string) => Promise<CommunityTopic>;
  authorName: string;
  authorAvatarUrl?: string;
  selectedTopic: CommunityTopic | null;
  setReplies: (replies: CommunityReply[]) => void;
}

export function useCommunityReplies({
  currentUserId,
  authorName,
  authorAvatarUrl,
  selectedTopic,
  setReplies,
  refreshTopic,
}: UseCommunityRepliesOptions) {
  const [replyPage, setReplyPage] = useState(1);
  const [replyTotal, setReplyTotal] = useState(0);
  const [replyLoading, setReplyLoading] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);
  // 被引用回复的 Post id。null 表示直接回复 Topic；楼层号只在展示层派生。
  const [replyTargetPostId, setReplyTargetPostId] = useState<string | null>(null);
  const [replyPublishing, setReplyPublishing] = useState(false);

  const resetReplies = useCallback(() => {
    setReplyTargetPostId(null);
    setReplyTotal(0);
    setReplyPage(1);
    setReplyError(null);
  }, []);

  const fetchReplies = useCallback(
    async (topicId: string, page: number) => {
      setReplyLoading(true);
      setReplyError(null);
      try {
        const result = await communityService.listReplies(topicId, {
          page,
          pageSize: COMMUNITY_REPLY_PAGE_SIZE,
        });
        setReplies(result.items);
        setReplyTotal(result.total);
        setReplyPage(page);
      } catch (error) {
        setReplyError(communityErrorMessage(error, '回复加载失败'));
      } finally {
        setReplyLoading(false);
      }
    },
    [setReplies],
  );

  const openFirstReplyPage = useCallback(async (topicId: string) => {
    setReplyTargetPostId(null);
    setReplyTotal(0);
    setReplyPage(1);
    setReplyError(null);
    await fetchReplies(topicId, 1);
  }, [fetchReplies]);

  const goToReplyPage = useCallback(
    async (page: number) => {
      if (!selectedTopic) return;
      setReplyTargetPostId(null);
      await fetchReplies(selectedTopic.id, page);
    },
    [fetchReplies, selectedTopic],
  );

  const startReply = useCallback((postId: string) => {
    if (postId) setReplyTargetPostId(postId);
  }, []);

  const cancelReply = useCallback(() => setReplyTargetPostId(null), []);

  const publishReply = useCallback(
    async (body: string) => {
      const topic = selectedTopic;
      if (!topic) return false;
      setReplyPublishing(true);
      try {
        await communityService.createReply({
          topicId: topic.id,
          body,
          authorId: currentUserId || 'current-user',
          authorName,
          authorAvatarUrl,
          replyToPostId: replyTargetPostId ?? undefined,
        });
        setReplyTargetPostId(null);
        await fetchReplies(topic.id, replyPage);
        notifySuccess(replyTargetPostId ? '回复楼层成功' : '主题回复成功');
        return true;
      } catch (error) {
        notifyError(communityErrorMessage(error, '回复发布失败'), { title: '回复失败' });
        return false;
      } finally {
        setReplyPublishing(false);
      }
    },
    [
      authorAvatarUrl,
      authorName,
      currentUserId,
      fetchReplies,
      replyPage,
      replyTargetPostId,
      selectedTopic,
    ],
  );

  const publishVote = useCallback(
    async (optionIndices: number[]) => {
      const topic = selectedTopic;
      if (!topic || topic.topicType !== 'poll') return false;
      const poll = topic.topicConfig?.poll;
      if (!poll) return false;
      const uniqueIndices = [...new Set(optionIndices)].sort((left, right) => left - right);
      const labels = uniqueIndices.map((index) => poll.options[index]);
      const selectionIsComplete =
        uniqueIndices.length === optionIndices.length && labels.every((label) => !!label);
      if (!uniqueIndices.length || !selectionIsComplete) return false;

      setReplyPublishing(true);
      try {
        await communityService.createReply({
          topicId: topic.id,
          body: `投票选择：${labels.join('、')}`,
          authorId: currentUserId || 'current-user',
          authorName,
          authorAvatarUrl,
          pollSelection: uniqueIndices,
        });
        setReplyTargetPostId(null);
        await fetchReplies(topic.id, replyPage);
        if (currentUserId) await refreshTopic(topic.id);
        notifySuccess('投票成功');
        return true;
      } catch (error) {
        notifyError(communityErrorMessage(error, '投票发布失败'), { title: '投票失败' });
        return false;
      } finally {
        setReplyPublishing(false);
      }
    },
    [
      authorAvatarUrl,
      authorName,
      currentUserId,
      fetchReplies,
      refreshTopic,
      replyPage,
      selectedTopic,
    ],
  );

  const replyPageCount = Math.max(1, Math.ceil(Math.max(0, replyTotal) / COMMUNITY_REPLY_PAGE_SIZE));

  return {
    resetReplies,
    openFirstReplyPage,
    goToReplyPage,
    replyPage,
    replyPageCount,
    replyTotal,
    replyLoading,
    replyError,
    replyTargetPostId,
    replyPublishing,
    startReply,
    cancelReply,
    publishReply,
    publishVote,
  };
}
