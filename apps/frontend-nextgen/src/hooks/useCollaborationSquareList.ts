import type {
  BotCatalogViewer,
  BotSearchMode,
  HumanBotActionContext,
  PublicBotSearchResultMode,
  SquareResource,
} from '@/domain/collaborationSquare/types';
import type { CollaborationSquareState } from '@/stores/collaborationSquareStore';
import type { HumanIdentityStatus } from '@/hooks/useHumanIdentity';
import { collaborationSquareBotService, collaborationSquareGroupService } from '@/services/collaborationSquare';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { getCollaborationSquareErrorMessage } from '@/utils/collaborationSquare';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useCollaborationSquarePagination } from './useCollaborationSquarePagination';

export const COLLABORATION_SQUARE_PAGE_SIZE = 24;
const COLLABORATION_SQUARE_SEARCH_DEBOUNCE_MS = 1_000;

interface UseCollaborationSquareListOptions
  extends Pick<CollaborationSquareState, 'setBots' | 'appendBots' | 'setGroups' | 'appendGroups' | 'setLoading' | 'setError'> {
  resource: SquareResource;
  humanBotContext: HumanBotActionContext | null;
  humanIdentityStatus: HumanIdentityStatus;
  botQuery: string;
  groupQuery: string;
  botSearchMode: BotSearchMode;
  /** Catalog 检索的当前身份（read-time viewer），映射为 BCS candidates 的视角 actor。 */
  viewer?: BotCatalogViewer;
  /** 透传时：智能搜索 + 空关键词不发请求、清空列表，交由调用方展示输入提示。 */
  gateSmartEmpty?: boolean;
}

export function useCollaborationSquareList({
  resource,
  humanBotContext,
  humanIdentityStatus,
  botQuery,
  groupQuery,
  botSearchMode,
  viewer,
  gateSmartEmpty,
  setBots,
  appendBots,
  setGroups,
  appendGroups,
  setLoading,
  setError,
}: UseCollaborationSquareListOptions) {
  const latestListRequest = useRef(0);
  const loadMoreController = useRef<AbortController | null>(null);
  const loadingMoreRef = useRef(false);
  const { currentBotOffset, currentGroupOffset, reloadPendingRef, hasMore, hasMoreRef, applyHasMore } =
    useCollaborationSquarePagination();
  // 智能搜索的 wire 结果模式（semantic/name_fallback/empty_query 本地短路）：独立保留，
  // 空结果也不丢失，供「名称匹配」提示与搜索栏展示。
  const [searchResultMode, setSearchResultMode] = useState<PublicBotSearchResultMode | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  const viewerFields = useMemo(
    () => (viewer ? { viewerActorType: viewer.viewerActorType, viewerActorId: viewer.viewerActorId } : {}),
    [viewer],
  );

  const executeLoad = useCallback(
    async (requestId: number, query: string, mode: BotSearchMode, signal?: AbortSignal) => {
      reloadPendingRef.current = false;
      loadMoreController.current?.abort();
      loadingMoreRef.current = false;
      setLoadingMore(false);
      setLoadMoreError(null);
      // 搜索词/角色/模式变化即新查询：Bot 游标复位到 0，不沿用旧查询的 nextOffset。
      currentBotOffset.current = 0;
      currentGroupOffset.current = 0;
      if (resource === 'bot' && gateSmartEmpty && mode === 'smart' && !query.trim()) {
        setBots([]);
        applyHasMore(false);
        setSearchResultMode('empty_query');
        setLoading(false);
        setError(null);
        return;
      }
      if (resource === 'bot' && !humanBotContext) {
        applyHasMore(false);
        setLoading(humanIdentityStatus === 'loading');
        setError(humanIdentityStatus === 'loading' ? null : '当前工作身份不可用，请刷新后重试');
        return;
      }
      setLoading(true);
      setError(null);
      try {
        if (resource === 'bot') {
          const keyword = query.trim();
          if (mode === 'smart' && keyword) {
            // 智能搜索是一次性批次（BCS 搜索容量上限 20）：不接分页，hasMore 恒 false。
            const result = await collaborationSquareBotService.discoverBots(
              { keyword, ...viewerFields },
              humanBotContext ?? undefined,
              signal,
            );
            if (requestId === latestListRequest.current && !signal?.aborted) {
              setBots(result.items);
              applyHasMore(false);
              setSearchResultMode(result.searchMode);
            }
          } else {
            const result = await collaborationSquareBotService.listBotPage(
              {
                ...(mode === 'name' && keyword ? { search: keyword } : {}),
                offset: 0,
                pageSize: COLLABORATION_SQUARE_PAGE_SIZE,
                ...viewerFields,
              },
              humanBotContext ?? undefined,
              signal,
            );
            if (requestId === latestListRequest.current && !signal?.aborted) {
              setBots(result.items);
              currentBotOffset.current = result.nextOffset;
              applyHasMore(result.hasMore);
              setSearchResultMode(null);
            }
          }
        } else if (resource === 'group') {
          const search = query.trim();
          const page = await collaborationSquareGroupService.listGroupPage(
            { ...(search ? { search } : {}), offset: 0, limit: COLLABORATION_SQUARE_PAGE_SIZE },
            signal,
          );
          if (requestId === latestListRequest.current && !signal?.aborted) {
            setGroups(page.items);
            currentGroupOffset.current = COLLABORATION_SQUARE_PAGE_SIZE;
            applyHasMore(currentGroupOffset.current < page.total);
          }
        }
      } catch (error) {
        if (requestId === latestListRequest.current && (error as Error).name !== 'AbortError') {
          applyHasMore(false);
          setSearchResultMode(null);
          setError(getCollaborationSquareErrorMessage(error));
        }
      } finally {
        if (requestId === latestListRequest.current) setLoading(false);
      }
    },
    [gateSmartEmpty, humanBotContext, humanIdentityStatus, resource, setBots, setError, setGroups, setLoading, viewerFields],
  );

  const activeLoadQuery = resource === 'bot' ? botQuery : groupQuery;

  const load = useCallback(
    (signal?: AbortSignal) => {
      const requestId = ++latestListRequest.current;
      return executeLoad(requestId, activeLoadQuery, botSearchMode, signal);
    },
    [activeLoadQuery, botSearchMode, executeLoad],
  );

  const loadMore = useCallback(async () => {
    if (reloadPendingRef.current || loadingMoreRef.current || hasMoreRef.current === false) return;
    if (resource === 'bot' && botSearchMode !== 'name') return;

    const requestId = latestListRequest.current;
    const controller = new AbortController();
    loadMoreController.current?.abort();
    loadMoreController.current = controller;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      if (resource === 'bot') {
        const offset = currentBotOffset.current;
        const keyword = botQuery.trim();
        const result = await collaborationSquareBotService.listBotPage(
          { ...(keyword ? { search: keyword } : {}), offset, pageSize: COLLABORATION_SQUARE_PAGE_SIZE, ...viewerFields },
          humanBotContext ?? undefined,
          controller.signal,
        );
        if (requestId !== latestListRequest.current || controller.signal.aborted) return;
        appendBots(result.items);
        // 续读游标保存 BCS 原始 nextOffset；hasMore 来自预读记录，不由 total 计算。
        currentBotOffset.current = result.nextOffset;
        applyHasMore(result.hasMore);
      } else if (resource === 'group') {
        const search = groupQuery.trim();
        const offset = currentGroupOffset.current;
        const result = await collaborationSquareGroupService.listGroupPage(
          { ...(search ? { search } : {}), offset, limit: COLLABORATION_SQUARE_PAGE_SIZE },
          controller.signal,
        );
        if (requestId !== latestListRequest.current || controller.signal.aborted) return;
        appendGroups(result.items);
        currentGroupOffset.current = offset + COLLABORATION_SQUARE_PAGE_SIZE;
        applyHasMore(currentGroupOffset.current < result.total);
      }
    } catch (error) {
      if (requestId === latestListRequest.current && (error as Error).name !== 'AbortError') {
        setLoadMoreError(getCollaborationSquareErrorMessage(error));
      }
    } finally {
      if (requestId === latestListRequest.current) {
        loadingMoreRef.current = false;
        setLoadingMore(false);
        if (loadMoreController.current === controller) loadMoreController.current = null;
      }
    }
  }, [appendBots, appendGroups, botQuery, botSearchMode, groupQuery, humanBotContext, resource, viewerFields]);

  // 深链定位经 ref 调用最新 loadMore，避免把不稳定的函数引用写进深链 effect 依赖。
  const loadMoreRef = useRef<() => void>(() => undefined);
  loadMoreRef.current = loadMore;

  useEffect(() => {
    // 任务广场由 useCollaborationSquareTask 独立加载，本 hook 仅服务 bot/group。
    if (resource === 'task') return;
    // 新查询立即失效旧游标与分页标记：防抖窗口内 IntersectionObserver 触发的续读不得沿用
    // 上一次查询的 nextOffset，也不得在首屏未返回时以 offset=0 重复追加。
    reloadPendingRef.current = true;
    currentBotOffset.current = 0;
    currentGroupOffset.current = 0;
    applyHasMore(false);
    const controller = new AbortController();
    const requestId = ++latestListRequest.current;
    const delay = activeLoadQuery.trim() ? COLLABORATION_SQUARE_SEARCH_DEBOUNCE_MS : 0;
    const timer = setTimeout(() => {
      void executeLoad(requestId, activeLoadQuery, botSearchMode, controller.signal);
    }, delay);
    return () => {
      clearTimeout(timer);
      controller.abort();
      loadMoreController.current?.abort();
    };
  }, [activeLoadQuery, applyHasMore, botSearchMode, executeLoad, resource]);

  useEffect(
    () => () => {
      latestListRequest.current += 1;
      loadMoreController.current?.abort();
      loadingMoreRef.current = false;
      useCollaborationSquareStore.getState().reset();
    },
    [],
  );

  return { load, loadMore, hasMore, hasMoreRef, loadMoreRef, loadingMore, loadMoreError, searchResultMode };
}
