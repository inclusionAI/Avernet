import { communityErrorMessage } from '@/domain/community/errors';
import { buildTopicListSummaryQueries } from '@/domain/community/interactionQueries';
import { COMMUNITY_PAGE_SIZE } from '@/domain/community/constants';
import type { CommunityTopic, ForumActorRef } from '@/domain/community/types';
import { communityService } from '@/services/community';
import type { UseCommunityInteractionsResult } from './useCommunityInteractions';
import { useCommunityStore } from '@/stores/communityStore';
import { useCallback, useEffect, useRef, useState } from 'react';

export function useCommunityTopicList({
  currentUserId,
  viewer,
  enrichTopicBotAuthor,
  botNameById,
  refreshInteractions,
}: {
  currentUserId: string;
  viewer: ForumActorRef;
  enrichTopicBotAuthor: (topic: CommunityTopic) => CommunityTopic;
  botNameById: Record<string, string>;
  refreshInteractions: UseCommunityInteractionsResult['refreshSummaries'];
}) {
  const {
    appendTopics,
    query,
    scope,
    setError,
    setLoading,
    setTopics,
    topicType,
    topics,
    total,
  } = useCommunityStore();
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const requestRef = useRef(0);
  // “关注”是一级视图：不继承其它主题类型筛选，展示已关注作者的全部类型主题。
  const listTopicType = scope === 'following' ? 'all' : topicType;

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    setLoadMoreError(null);
    try {
      const page = await communityService.listTopics({
        search: query,
        scope: scope,
        topicType: listTopicType,
        // "我的"走 openapi §2.4 author_id 真分页：下发本人工号，后端过滤、total 为过滤后行数。
        ...(scope === 'mine' && currentUserId
          ? { authorId: currentUserId }
          : {}),
        ...(scope === 'following' && currentUserId
          ? { followingOwnerId: currentUserId }
          : {}),
        offset: 0,
        limit: COMMUNITY_PAGE_SIZE,
      });
      if (requestId === requestRef.current) {
        const items = page.items.map(enrichTopicBotAuthor);
        setTopics(items, page.total);
        setHasMore(items.length > 0 && items.length < page.total);
        await refreshInteractions(buildTopicListSummaryQueries(items, viewer));
      }
    } catch (error) {
      if (requestId === requestRef.current) {
        setError(communityErrorMessage(error, '社区加载失败，请稍后重试'));
      }
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, [
    currentUserId,
    viewer,
    enrichTopicBotAuthor,
    refreshInteractions,
    query,
    scope,
    listTopicType,
  ]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 200);
    return () => window.clearTimeout(timer);
  }, [load]);

  // BOT 作者名晚到补丁：本人 bot 列表通常在 topics 之后异步到达；到齐后回补已渲染主题的作者展示名。
  useEffect(() => {
    if (Object.keys(botNameById).length === 0 || topics.length === 0) return;
    let changed = false;
    const enriched = topics.map((topic) => {
      if (topic.author.type !== 'bot') return topic;
      const name = botNameById[topic.author.id];
      if (name && name !== topic.author.displayName) {
        changed = true;
        return { ...topic, author: { ...topic.author, displayName: name } };
      }
      return topic;
    });
    if (changed) setTopics(enriched, total);
  }, [botNameById, setTopics, topics, total]);

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const previousCount = topics.length;
      const page = await communityService.listTopics({
        search: query,
        scope: scope,
        topicType: listTopicType,
        ...(scope === 'mine' && currentUserId
          ? { authorId: currentUserId }
          : {}),
        ...(scope === 'following' && currentUserId
          ? { followingOwnerId: currentUserId }
          : {}),
        offset: previousCount,
        limit: COMMUNITY_PAGE_SIZE,
      });
      const appended = page.items.map(enrichTopicBotAuthor);
      appendTopics(appended);
      await refreshInteractions(buildTopicListSummaryQueries(appended, viewer));
      const loaded = previousCount + appended.length;
      setHasMore(appended.length > 0 && loaded < page.total);
    } catch (error) {
      setLoadMoreError(communityErrorMessage(error, '加载更多失败，请稍后重试'));
    } finally {
      setLoadingMore(false);
    }
  }, [
    appendTopics,
    currentUserId,
    viewer,
    enrichTopicBotAuthor,
    hasMore,
    refreshInteractions,
    loadingMore,
    query,
    scope,
    listTopicType,
    topics,
  ]);

  return {
    load,
    loadMore,
    hasMore,
    loadingMore,
    loadMoreError,
  };
}
