import { notifyError, notifySuccess } from '@/components/ui/notify';
import { enrichOwnership } from '@/domain/community/ownership';
import { communityErrorMessage } from '@/domain/community/errors';
import { COMMUNITY_PAGE_SIZE } from '@/domain/community/constants';
import type { CommunityAuthor, CommunityTopic, CreateCommunityTopicDraft } from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCommunityStore } from '@/stores/communityStore';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useBotAuthorEnricher } from './useBotAuthorEnricher';
import { useCommunityReplies } from './useCommunityReplies';
import { useHumanIdentity } from './useHumanIdentity';

export { COMMUNITY_PAGE_SIZE, COMMUNITY_REPLY_PAGE_SIZE } from '@/domain/community/constants';

export function useCommunity() {
  const store = useCommunityStore();
  const { identity } = useHumanIdentity();
  const currentUserId = identity?.userId.trim() ?? '';
  // BOT 作者名兜底：后端 §4.1 不返 BOT作者 display_name 时，用本人 owned bots 的 bot_name 兜底。
  const { enrichTopicBotAuthor, botNameById } = useBotAuthorEnricher(currentUserId);

  const [publishing, setPublishing] = useState(false);
  const [closing, setClosing] = useState(false);
  const [helpfulPostId, setHelpfulPostId] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const requestRef = useRef(0);

  const fetchTopicWithViewer = useCallback(
    async (topicId: string) => {
      const options = currentUserId
        ? { viewerAuthorId: currentUserId, viewerAuthorType: 'HUMAN' as const }
        : {};
      const topic = await communityService.getTopic(topicId, options);
      store.setSelectedTopic(enrichOwnership(enrichTopicBotAuthor(topic), currentUserId));
      return topic;
    },
    [currentUserId, enrichTopicBotAuthor, store.setSelectedTopic],
  );

  const replies = useCommunityReplies({
    selectedTopic: store.selectedTopic,
    setReplies: store.setReplies,
  });
  const { resetReplies, openFirstReplyPage, goToReplyPage, replyPage } = replies;

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    store.setLoading(true);
    store.setError(null);
    setLoadMoreError(null);
    try {
      const page = await communityService.listTopics({
        search: store.query,
        scope: store.scope,
        topicType: store.topicType,
        // “我的”走 openapi §2.4 author_id 真分页：下发本人工号，后端过滤、total 为过滤后行数。
        ...(store.scope === 'mine' && currentUserId ? { authorId: currentUserId } : {}),
        offset: 0,
        limit: COMMUNITY_PAGE_SIZE,
      });
      if (requestId === requestRef.current) {
        const items = page.items.map(enrichTopicBotAuthor);
        store.setTopics(items, page.total);
        setHasMore(items.length > 0 && items.length < page.total);
      }
    } catch (error) {
      if (requestId === requestRef.current) {
        store.setError(communityErrorMessage(error, '社区加载失败，请稍后重试'));
      }
    } finally {
      if (requestId === requestRef.current) store.setLoading(false);
    }
  }, [
    currentUserId,
    enrichTopicBotAuthor,
    store.setError,
    store.setLoading,
    store.setTopics,
    store.query,
    store.scope,
    store.topicType,
  ]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 200);
    return () => window.clearTimeout(timer);
  }, [load]);

  // BOT 作者名晚到补丁：本人 bot 列表通常在 topics 之后异步到达；到齐后回补已渲染主题的作者展示名。
  useEffect(() => {
    if (Object.keys(botNameById).length === 0 || store.topics.length === 0) return;
    let changed = false;
    const enriched = store.topics.map((topic) => {
      if (topic.author.type !== 'bot') return topic;
      const name = botNameById[topic.author.id];
      if (name && name !== topic.author.displayName) {
        changed = true;
        return { ...topic, author: { ...topic.author, displayName: name } };
      }
      return topic;
    });
    if (changed) store.setTopics(enriched, store.total);
  }, [botNameById, store.setTopics, store.topics, store.total]);

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const previousCount = store.topics.length;
      const page = await communityService.listTopics({
        search: store.query,
        scope: store.scope,
        topicType: store.topicType,
        ...(store.scope === 'mine' && currentUserId ? { authorId: currentUserId } : {}),
        offset: previousCount,
        limit: COMMUNITY_PAGE_SIZE,
      });
      const appended = page.items.map(enrichTopicBotAuthor);
      store.appendTopics(appended);
      const loaded = previousCount + appended.length;
      setHasMore(appended.length > 0 && loaded < page.total);
    } catch (error) {
      setLoadMoreError(communityErrorMessage(error, '加载更多失败，请稍后重试'));
    } finally {
      setLoadingMore(false);
    }
  }, [
    currentUserId,
    enrichTopicBotAuthor,
    hasMore,
    loadingMore,
    store.appendTopics,
    store.query,
    store.scope,
    store.topicType,
    store.topics.length,
  ]);

  const openTopic = useCallback(
    async (topic: CommunityTopic) => {
      const owned = enrichOwnership(topic, currentUserId);
      store.setSelectedTopic(owned);
      store.setDetailLoading(true);
      store.setDetailError(null);
      resetReplies();
      try {
        await fetchTopicWithViewer(topic.id);
        await openFirstReplyPage(topic.id);
      } catch (error) {
        store.setDetailError(communityErrorMessage(error, '主题详情加载失败'));
      } finally {
        store.setDetailLoading(false);
      }
    },
    [
      currentUserId,
      fetchTopicWithViewer,
      openFirstReplyPage,
      resetReplies,
      store.setDetailError,
      store.setDetailLoading,
      store.setSelectedTopic,
    ],
  );

  const closeDetail = useCallback(() => store.setSelectedTopic(null), [store.setSelectedTopic]);

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
        notifyError(communityErrorMessage(error, '主题发布失败'), { title: '发布失败' });
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
      notifySuccess(topic.topicType === 'help' ? '求助已标记为已解决' : '主题已结帖');
      return true;
    } catch (error) {
      const helpTopic = topic.topicType === 'help';
      notifyError(communityErrorMessage(error, helpTopic ? '标记已解决失败' : '结帖失败'), {
        title: helpTopic ? '标记已解决失败' : '结帖失败',
      });
      return false;
    } finally {
      setClosing(false);
    }
  }, [store.markTopicClosed, store.selectedTopic]);

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
        notifyError(communityErrorMessage(error, '有帮助状态更新失败'), { title: '操作失败' });
        return false;
      } finally {
        setHelpfulPostId(null);
      }
    },
    [goToReplyPage, replyPage, store.selectedTopic],
  );

  return {
    ...store,
    ...replies,
    currentUserId,
    load,
    loadMore,
    hasMore,
    loadingMore,
    loadMoreError,
    openTopic,
    closeDetail,
    publishTopic,
    closeTopic,
    setPostHelpful,
    publishing,
    closing,
    helpfulPostId,
  };
}
