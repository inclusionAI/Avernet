import { notifyError, notifySuccess } from '@/components/ui/notify';
import { enrichOwnership } from '@/domain/community/ownership';
import type { CommunityAuthor, CommunityTopic } from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCommunityStore } from '@/stores/communityStore';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useBotAuthorEnricher } from './useBotAuthorEnricher';
import { useHumanIdentity } from './useHumanIdentity';

/** 主题列表分页大小（首屏 + 加载更多统一）。验证分页期间保持 3，验证完成改回 20。 */
export const COMMUNITY_PAGE_SIZE = 3;
/** 回帖分页大小（详情页翻页）。楼层号 = (replyPage-1)*COMMUNITY_REPLY_PAGE_SIZE + index + 1。当前为验证分页临时调小为 5，验证后改回 20。 */
export const COMMUNITY_REPLY_PAGE_SIZE = 5;

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function useCommunity() {
  const store = useCommunityStore();
  const { identity } = useHumanIdentity();
  const currentUserId = identity?.userId.trim() ?? ''; // BOT 作者名兜底：后端 §4.1 不返 BOT作者 display_name 时，用本人 owned bots 的 bot_name 兜底（doc §8 验收）。
  const { enrichTopicBotAuthor, botNameById } = useBotAuthorEnricher(currentUserId);

  const [publishing, setPublishing] = useState(false);
  const [closing, setClosing] = useState(false);
  // 列表无限滚动
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  // 详情回帖分页
  const [replyPage, setReplyPage] = useState(1);
  const [replyTotal, setReplyTotal] = useState(0);
  const [replyLoading, setReplyLoading] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);
  const requestRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    store.setLoading(true);
    store.setError(null);
    setLoadMoreError(null);
    try {
      const page = await communityService.listTopics({
        search: store.query,
        scope: store.scope,
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
      if (requestId === requestRef.current) store.setError(errorMessage(error, '社区加载失败，请稍后重试'));
    } finally {
      if (requestId === requestRef.current) store.setLoading(false);
    }
  }, [currentUserId, store.query, store.scope, store.setError, store.setLoading, store.setTopics]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 200);
    return () => window.clearTimeout(timer);
  }, [load]);

  // BOT 作者名晚到补丁：本人 bot 列表通常在 topics 之后异步到达；到齐后回补已渲染主题的作者展示名。
  useEffect(() => {
    if (Object.keys(botNameById).length === 0 || store.topics.length === 0) return;
    let changed = false;
    const enriched = store.topics.map((t) => {
      if (t.author.type !== 'bot') return t;
      const name = botNameById[t.author.id];
      if (name && name !== t.author.displayName) {
        changed = true;
        return { ...t, author: { ...t.author, displayName: name } };
      }
      return t;
    });
    if (changed) store.setTopics(enriched, store.total);
  }, [botNameById, store.topics, store.total, store.setTopics]);

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const previousCount = store.topics.length;
      const page = await communityService.listTopics({
        search: store.query,
        scope: store.scope,
        ...(store.scope === 'mine' && currentUserId ? { authorId: currentUserId } : {}),
        offset: previousCount,
        limit: COMMUNITY_PAGE_SIZE,
      });
      const appended = page.items.map(enrichTopicBotAuthor);
      store.appendTopics(appended);
      const loaded = previousCount + appended.length;
      setHasMore(appended.length > 0 && loaded < page.total);
    } catch (error) {
      setLoadMoreError(errorMessage(error, '加载更多失败，请稍后重试'));
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
    store.topics.length,
  ]);

  const fetchReplies = useCallback(
    async (topicId: string, page: number) => {
      setReplyLoading(true);
      setReplyError(null);
      try {
        const result = await communityService.listReplies(topicId, { page, pageSize: COMMUNITY_REPLY_PAGE_SIZE });
        store.setReplies(result.items);
        setReplyTotal(result.total);
        setReplyPage(page);
      } catch (error) {
        setReplyError(errorMessage(error, '回复加载失败'));
      } finally {
        setReplyLoading(false);
      }
    },
    [store.setReplies],
  );

  const openTopic = useCallback(
    async (topic: CommunityTopic) => {
      const owned = enrichOwnership(topic, currentUserId);
      store.setSelectedTopic(owned);
      store.setDetailLoading(true);
      store.setDetailError(null);
      setReplyError(null);
      setReplyTotal(0);
      setReplyPage(1);
      try {
        const detail = await communityService.getTopic(topic.id);
        store.setSelectedTopic(enrichOwnership(enrichTopicBotAuthor(detail), currentUserId));
        await fetchReplies(topic.id, 1);
      } catch (error) {
        store.setDetailError(errorMessage(error, '主题详情加载失败'));
      } finally {
        store.setDetailLoading(false);
      }
    },
    [
      currentUserId,
      enrichTopicBotAuthor,
      fetchReplies,
      store.setDetailError,
      store.setDetailLoading,
      store.setSelectedTopic,
    ],
  );

  const closeDetail = useCallback(() => store.setSelectedTopic(null), [store.setSelectedTopic]);

  const goToReplyPage = useCallback(
    async (page: number) => {
      const topic = store.selectedTopic;
      if (!topic) return;
      await fetchReplies(topic.id, page);
    },
    [fetchReplies, store.selectedTopic],
  );

  const publishTopic = useCallback(
    async (title: string, body: string) => {
      setPublishing(true);
      try {
        const author: CommunityAuthor = {
          type: 'human',
          id: currentUserId || 'current-user',
          displayName: identity?.displayName || '我',
          avatarUrl: identity?.avatarUrl,
        };
        const topic = await communityService.createTopic({
          title,
          body,
          authorId: author.id,
          authorName: author.displayName,
          authorAvatarUrl: author.avatarUrl,
        });
        // 新发布的主题必为本人 → isMine/canClose 直接置真，无需依赖后端未返的 is_mine。
        store.prependTopic(enrichOwnership(topic, currentUserId));
        notifySuccess('主题发布成功');
        return true;
      } catch (error) {
        notifyError(errorMessage(error, '主题发布失败'), { title: '发布失败' });
        return false;
      } finally {
        setPublishing(false);
      }
    },
    [currentUserId, identity?.avatarUrl, identity?.displayName, store.prependTopic],
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
      notifySuccess('主题已结帖');
      return true;
    } catch (error) {
      notifyError(errorMessage(error, '结帖失败'), { title: '结帖失败' });
      return false;
    } finally {
      setClosing(false);
    }
  }, [currentUserId, store.markTopicClosed, store.selectedTopic]);

  const replyPageCount = Math.max(1, Math.ceil(Math.max(0, replyTotal) / COMMUNITY_REPLY_PAGE_SIZE));

  return {
    ...store,
    load,
    loadMore,
    hasMore,
    loadingMore,
    loadMoreError,
    openTopic,
    closeDetail,
    goToReplyPage,
    replyPage,
    replyPageCount,
    replyTotal,
    replyLoading,
    replyError,
    publishTopic,
    closeTopic,
    publishing,
    closing,
  };
}
