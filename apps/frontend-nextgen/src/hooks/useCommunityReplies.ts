import { COMMUNITY_REPLY_PAGE_SIZE } from '@/domain/community/constants';
import { communityErrorMessage } from '@/domain/community/errors';
import type { CommunityReply, CommunityTopic } from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCallback, useState } from 'react';

interface UseCommunityRepliesOptions {
  selectedTopic: CommunityTopic | null;
  setReplies: (replies: CommunityReply[]) => void;
}

export function useCommunityReplies({
  selectedTopic,
  setReplies,
}: UseCommunityRepliesOptions) {
  const [replyPage, setReplyPage] = useState(1);
  const [replyTotal, setReplyTotal] = useState(0);
  const [replyLoading, setReplyLoading] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);

  const resetReplies = useCallback(() => {
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
    setReplyTotal(0);
    setReplyPage(1);
    setReplyError(null);
    await fetchReplies(topicId, 1);
  }, [fetchReplies]);

  const goToReplyPage = useCallback(
    async (page: number) => {
      if (!selectedTopic) return;
      await fetchReplies(selectedTopic.id, page);
    },
    [fetchReplies, selectedTopic],
  );

  const replyPageCount = Math.max(
    1,
    Math.ceil(Math.max(0, replyTotal) / COMMUNITY_REPLY_PAGE_SIZE),
  );

  return {
    resetReplies,
    openFirstReplyPage,
    goToReplyPage,
    replyPage,
    replyPageCount,
    replyTotal,
    replyLoading,
    replyError,
  };
}
