import { notifyError, notifySuccess } from '@/components/ui/notify';
import { communityErrorMessage } from '@/domain/community/errors';
import { enrichOwnership } from '@/domain/community/ownership';
import type {
  CommunityAuthor,
  CreateCommunityTopicDraft,
  ForumActorRef,
} from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCommunityStore } from '@/stores/communityStore';
import { useCallback, useState } from 'react';
import { useBotAuthorEnricher } from './useBotAuthorEnricher';
import { useCommunityInteractions } from './useCommunityInteractions';
import { useCommunityTopicDetail } from './useCommunityTopicDetail';
import { useCommunityTopicList } from './useCommunityTopicList';
import { useHumanIdentity } from './useHumanIdentity';

export { COMMUNITY_PAGE_SIZE, COMMUNITY_REPLY_PAGE_SIZE } from '@/domain/community/constants';

export function useCommunity() {
  const store = useCommunityStore();
  const { identity } = useHumanIdentity();
  const currentUserId = identity?.userId.trim() ?? '';
  // BOT 作者名兜底：后端 §4.1 不返 BOT作者 display_name 时，用本人 owned bots 的 bot_name 兜底。
  const { enrichTopicBotAuthor, botNameById } = useBotAuthorEnricher(currentUserId);
  const interactionViewer: ForumActorRef = {
    actorType: 'HUMAN',
    id: currentUserId || 'current-user',
  };
  const interactions = useCommunityInteractions({ viewer: interactionViewer });
  const topicList = useCommunityTopicList({
    currentUserId,
    enrichTopicBotAuthor,
    botNameById,
  });
  const topicDetail = useCommunityTopicDetail({
    currentUserId,
    viewer: interactionViewer,
    interactions,
    enrichTopicBotAuthor,
  });

  const [publishing, setPublishing] = useState(false);
  const [closing, setClosing] = useState(false);

  const publishTopic = useCallback(
    async (draft: CreateCommunityTopicDraft) => {
      setPublishing(true);
      try {
        const author: CommunityAuthor = {
          type: 'human',
          id: currentUserId || 'current-user',
          displayName: identity?.displayName || '我',
          avatarUrl: identity?.avatarUrl,
        };
        const topic = await communityService.createTopic({
          ...draft,
          authorId: author.id,
          authorName: author.displayName,
          authorAvatarUrl: author.avatarUrl,
        });
        // 新发布的主题必为本人 → isMine/canClose 直接置真，无需依赖后端未返的 is_mine。
        if (store.topicType === 'all' || store.topicType === topic.topicType) {
          store.prependTopic(enrichOwnership(topic, currentUserId));
        }
        notifySuccess('主题发布成功');
        return true;
      } catch (error) {
        notifyError(communityErrorMessage(error, '主题发布失败'), {
          title: '发布失败',
        });
        return false;
      } finally {
        setPublishing(false);
      }
    },
    [
      currentUserId,
      identity?.avatarUrl,
      identity?.displayName,
      store.prependTopic,
      store.topicType,
    ],
  );

  const closeTopic = useCallback(async () => {
    const topic = store.selectedTopic;
    if (!topic?.canClose) return false;
    setClosing(true);
    try {
      // openapi §2.3 写口 body 由 controller 补 author_type('HUMAN')；author_id 取主题作者。
      // canClose 守卫已确保作者=当前登录人，无需 'unknown' 回退。
      await communityService.closeTopic(topic.id, topic.author.id);
      store.markTopicClosed(topic.id);
      notifySuccess(
        topic.topicType === 'help' ? '求助已标记为已解决' : '主题已结帖',
      );
      return true;
    } catch (error) {
      const helpTopic = topic.topicType === 'help';
      notifyError(
        communityErrorMessage(error, helpTopic ? '标记已解决失败' : '结帖失败'),
        { title: helpTopic ? '标记已解决失败' : '结帖失败' },
      );
      return false;
    } finally {
      setClosing(false);
    }
  }, [store.markTopicClosed, store.selectedTopic]);

  return {
    ...store,
    ...topicList,
    ...topicDetail,
    currentUserId,
    publishTopic,
    closeTopic,
    publishing,
    closing,
    interactions,
  };
}
