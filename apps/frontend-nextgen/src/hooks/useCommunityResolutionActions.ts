import { notifyError, notifySuccess } from '@/components/ui/notify';
import { communityErrorMessage } from '@/domain/community/errors';
import type { CommunityTopic } from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCallback, useState } from 'react';

interface UseCommunityResolutionActionsOptions {
  selectedTopic: CommunityTopic | null;
  markTopicClosed: (topicId: string) => void;
  goToReplyPage: (page: number) => Promise<void>;
  replyPage: number;
}

export function useCommunityResolutionActions({
  selectedTopic,
  markTopicClosed,
  goToReplyPage,
  replyPage,
}: UseCommunityResolutionActionsOptions) {
  const [closing, setClosing] = useState(false);
  const [helpfulPostId, setHelpfulPostId] = useState<string | null>(null);

  const closeTopic = useCallback(async () => {
    if (!selectedTopic?.canClose) return false;
    setClosing(true);
    try {
      // openapi §2.3 写口 body 由 controller 补 author_type('HUMAN')；author_id 取主题作者。
      // canClose 守卫已确保作者=当前登录人，无需 'unknown' 回退。
      await communityService.closeTopic(selectedTopic.id, selectedTopic.author.id);
      markTopicClosed(selectedTopic.id);
      notifySuccess(selectedTopic.topicType === 'help' ? '求助已标记为已解决' : '主题已结帖');
      return true;
    } catch (error) {
      const helpTopic = selectedTopic.topicType === 'help';
      notifyError(communityErrorMessage(error, helpTopic ? '标记已解决失败' : '结帖失败'), {
        title: helpTopic ? '标记已解决失败' : '结帖失败',
      });
      return false;
    } finally {
      setClosing(false);
    }
  }, [markTopicClosed, selectedTopic]);

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
        notifyError(communityErrorMessage(error, '有帮助状态更新失败'), { title: '操作失败' });
        return false;
      } finally {
        setHelpfulPostId(null);
      }
    },
    [goToReplyPage, replyPage, selectedTopic],
  );

  return {
    closeTopic,
    setPostHelpful,
    closing,
    helpfulPostId,
  };
}
