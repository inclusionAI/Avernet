import {
  collaborationCandidateService,
  type CollaborationBotView,
} from '@/services/workspace/collaborationCandidateService';
import { useCallback, useEffect, useRef, useState } from 'react';

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 350;

const appendUnique = (current: CollaborationBotView[], incoming: CollaborationBotView[]) => [
  ...current,
  ...incoming.filter((bot) => !current.some((item) => item.id === bot.id)),
];

export interface UseDiscoveryBotsResult {
  bots: CollaborationBotView[];
  isLoading: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  error?: string;
  retry: () => void;
  loadMore: () => void;
  /** 已发送申请的 Bot id 集合（成功后本地标记，避免重复点击）。 */
  requestedIds: Set<string>;
  sendFriendRequest: (botId: string) => Promise<boolean>;
}

/**
 * 添加好友弹窗（Bot 广场）数据层 Hook：purpose=discovery 首屏 + 名称防抖搜索 + 上拉分页。
 * 智能搜索暂不实现（PRD 占位），名称搜索经 name 查询参数下发。
 */
export function useDiscoveryBots(
  actorId: string | null | undefined,
  enabled: boolean,
  search: string,
): UseDiscoveryBotsResult {
  const [bots, setBots] = useState<CollaborationBotView[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const [retryNonce, setRetryNonce] = useState(0);
  const [requestedIds, setRequestedIds] = useState<Set<string>>(new Set());
  const offsetRef = useRef(0);
  const searchSeenRef = useRef<string>(search);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadFirstPage = useCallback(
    (name: string) => {
      if (!actorId) return;
      setIsLoading(true);
      setError(undefined);
      offsetRef.current = 0;
      collaborationCandidateService
        .listDiscoveryBots(actorId, { name: name || undefined, offset: 0, limit: PAGE_SIZE })
        .then((res) => {
          if (!res.ok) {
            setBots([]);
            setHasMore(false);
            setError(res.error.friendlyMessage);
            return;
          }
          setBots(res.data.items);
          offsetRef.current = res.data.offset + res.data.items.length;
          setHasMore(res.data.hasMore);
        })
        .finally(() => setIsLoading(false));
    },
    [actorId],
  );

  // 首屏 + 重试
  useEffect(() => {
    if (!enabled || !actorId) return;
    loadFirstPage('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, actorId, retryNonce]);

  // 搜索防抖：仅 enabled 且文本真正变化时触发，重置到第一页。
  useEffect(() => {
    if (!enabled || !actorId) return;
    if (search === searchSeenRef.current) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      searchSeenRef.current = search;
      loadFirstPage(search);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [enabled, actorId, search, loadFirstPage]);

  const loadMore = useCallback(() => {
    if (!enabled || !actorId || isLoadingMore || isLoading || !hasMore) return;
    setIsLoadingMore(true);
    collaborationCandidateService
      .listDiscoveryBots(actorId, {
        name: searchSeenRef.current || undefined,
        offset: offsetRef.current,
        limit: PAGE_SIZE,
      })
      .then((res) => {
        if (!res.ok) {
          setError(res.error.friendlyMessage);
          return;
        }
        setBots((cur) => appendUnique(cur, res.data.items));
        offsetRef.current = res.data.offset + res.data.items.length;
        setHasMore(res.data.hasMore);
      })
      .finally(() => setIsLoadingMore(false));
  }, [enabled, actorId, isLoadingMore, isLoading, hasMore]);

  const retry = useCallback(() => setRetryNonce((n) => n + 1), []);

  const sendFriendRequest = useCallback(
    async (botId: string): Promise<boolean> => {
      if (!actorId) return false;
      const res = await collaborationCandidateService.sendFriendRequest(actorId, botId);
      if (!res.ok) return false;
      setRequestedIds((cur) => new Set(cur).add(botId));
      return true;
    },
    [actorId],
  );

  return {
    bots,
    isLoading,
    isLoadingMore,
    hasMore,
    error,
    retry,
    loadMore,
    requestedIds,
    sendFriendRequest,
  };
}
