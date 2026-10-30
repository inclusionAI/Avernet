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
import { useCallback, useMemo, useState } from 'react';
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
  /** 当前登录人。Avatar 由 HumanIdentity capability 提供，社交媒体头像可直接命中。 */
  const currentUser: CommunityAuthor | null = currentUserId
    ? {
        type: 'human',
        id: currentUserId,
        displayName: identity?.displayName || '我',
        avatarUrl: identity?.avatarUrl,
      }
    : null;
  // BOT 作者名兜底：后端 §4.1 不返 BOT作者 display_name 时，用本人 owned bots 的 bot_name 兜底。
  const { enrichTopicBotAuthor, botNameById } = useBotAuthorEnricher(currentUserId);
  const interactionViewer = useMemo<ForumActorRef>(
    () => ({
      actorType: 'HUMAN',
      id: currentUserId || 'current-user',
    }),
    [currentUserId],
  );
  const interactions = useCommunityInteractions({ viewer: interactionViewer });
  const topicList = useCommunityTopicList({
    currentUserId,
    viewer: interactionViewer,
    enrichTopicBotAuthor,
    botNameById,
    refreshInteractions: interactions.refreshSummaries,
  });
  const topicDetail = useCommunityTopicDetail({
    currentUserId,
    currentAuthorName: identity?.displayName,
    viewer: interactionViewer,
    interactions,
    enrichTopicBotAuthor,
  });

  const [publishing, setPublishing] = useState(false);
  const [closing, setClosing] = useState(false);
  const [editSaving, setEditSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

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

  const updateTopic = useCallback(
    async (draft: { title: string; body: string }) => {
      const topic = store.selectedTopic;
      if (!topic?.canEdit) return false;
      setEditSaving(true);
      try {
        const updated = await communityService.updateTopic({
          topicId: topic.id,
          authorId: topic.author.id,
          title: draft.title,
          body: draft.body,
        });
        store.replaceTopic(enrichOwnership(updated, currentUserId));
        notifySuccess('主题已更新');
        return true;
      } catch (error) {
        notifyError(communityErrorMessage(error, '主题更新失败'), {
          title: '更新失败',
        });
        return false;
      } finally {
        setEditSaving(false);
      }
    },
    [currentUserId, store.replaceTopic, store.selectedTopic],
  );

  const deleteTopic = useCallback(async () => {
    const topic = store.selectedTopic;
    if (!topic?.canDelete) return false;
    setDeleting(true);
    try {
      await communityService.deleteTopic(topic.id, topic.author.id);
      store.removeTopic(topic.id);
      notifySuccess('主题已删除');
      return true;
    } catch (error) {
      notifyError(communityErrorMessage(error, '主题删除失败，请稍后重试'), {
        title: '删除失败',
      });
      return false;
    } finally {
      setDeleting(false);
    }
  }, [store.removeTopic, store.selectedTopic]);

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
    currentUser,
    currentUserId,
    publishTopic,
    updateTopic,
    deleteTopic,
    closeTopic,
    publishing,
    closing,
    editSaving,
    deleting,
    interactions,
  };
}
