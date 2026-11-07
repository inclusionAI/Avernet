import type {
  BotCatalogViewer,
  BotSearchMode,
  HumanBotActionContext,
  SquareResource,
} from '@/domain/collaborationSquare/types';
import type { HumanIdentityStatus } from '@/hooks/useHumanIdentity';
import { collaborationSquareBotService, collaborationSquareGroupService } from '@/services/collaborationSquare';
import type { CollaborationSquareState } from '@/stores/collaborationSquareStore';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export const COLLABORATION_SQUARE_PAGE_SIZE = 24;
const COLLABORATION_SQUARE_SEARCH_DEBOUNCE_MS = 1_000;

interface UseCollaborationSquareListOptions {
  resource: SquareResource;
  humanBotContext: HumanBotActionContext | null;
  humanIdentityStatus: HumanIdentityStatus;
  botQuery: string;
  groupQuery: string;
  botSearchMode: BotSearchMode;
  /** Catalog 检索的当前身份（read-time viewer），下发给 bot-catalog 的 viewer_actor_* 参数。 */
  viewer?: BotCatalogViewer;
  /** 透传时：智能搜索 + 空关键词不发请求、清空列表，交由调用方展示输入提示。 */
  gateSmartEmpty?: boolean;
  setBots: CollaborationSquareState['setBots'];
  appendBots: CollaborationSquareState['appendBots'];
  setGroups: CollaborationSquareState['setGroups'];
  appendGroups: CollaborationSquareState['appendGroups'];
  setLoading: CollaborationSquareState['setLoading'];
  setError: CollaborationSquareState['setError'];
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
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
  const currentBotPage = useRef(1);
  const currentGroupOffset = useRef(0);
  const loadMoreController = useRef<AbortController | null>(null);
  const loadingMoreRef = useRef(false);
  const [hasMore, setHasMore] = useState(false);
  // 分页标记的同步镜像：executeLoad 里 setBots（store 同步通知）与 setHasMore（批处理）可能
  // 分属两次渲染，深链定位与 loadMore 守卫需在同一轮次读到实时值，避免提前判定目标失效。
  const hasMoreRef = useRef(false);
  const applyHasMore = useCallback((value: boolean) => {
    hasMoreRef.current = value;
    setHasMore(value);
  }, []);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  const viewerFields = useMemo(
    () => (viewer ? { viewerActorType: viewer.viewerActorType, viewerActorId: viewer.viewerActorId } : {}),
    [viewer],
  );

  const executeLoad = useCallback(
    async (requestId: number, query: string, mode: BotSearchMode, signal?: AbortSignal) => {
      loadMoreController.current?.abort();
      loadingMoreRef.current = false;
      setLoadingMore(false);
      setLoadMoreError(null);
      currentBotPage.current = 1;
      currentGroupOffset.current = 0;
      if (resource === 'bot' && gateSmartEmpty && mode === 'smart' && !query.trim()) {
        setBots([]);
        applyHasMore(false);
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
          const isSmartSearch = mode === 'smart' && Boolean(keyword);
          const page = isSmartSearch
            ? {
                items: await collaborationSquareBotService.discoverBots(
                  { keyword, topK: 20, minScore: 0.01, runtimeState: 'online', ...viewerFields },
                  humanBotContext ?? undefined,
                  signal,
                ),
                total: 0,
              }
            : await collaborationSquareBotService.listBotPage(
                {
                  ...(mode === 'name' && keyword ? { search: keyword } : {}),
                  page: 1,
                  pageSize: COLLABORATION_SQUARE_PAGE_SIZE,
                  ...viewerFields,
                },
                humanBotContext ?? undefined,
                signal,
              );
          if (requestId === latestListRequest.current && !signal?.aborted) {
            setBots(page.items);
            applyHasMore(!isSmartSearch && COLLABORATION_SQUARE_PAGE_SIZE < page.total);
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
          setError(errorMessage(error));
        }
      } finally {
        if (requestId === latestListRequest.current) setLoading(false);
      }
    },
    [
      gateSmartEmpty,
      humanBotContext,
      humanIdentityStatus,
      resource,
      setBots,
      setError,
      setGroups,
      setLoading,
      viewerFields,
    ],
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
    if (loadingMoreRef.current || hasMoreRef.current === false) return;
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
        const page = currentBotPage.current + 1;
        const keyword = botQuery.trim();
        const result = await collaborationSquareBotService.listBotPage(
          { ...(keyword ? { search: keyword } : {}), page, pageSize: COLLABORATION_SQUARE_PAGE_SIZE, ...viewerFields },
          humanBotContext ?? undefined,
          controller.signal,
        );
        if (requestId !== latestListRequest.current || controller.signal.aborted) return;
        appendBots(result.items);
        currentBotPage.current = page;
        applyHasMore(page * COLLABORATION_SQUARE_PAGE_SIZE < result.total);
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
        setLoadMoreError(errorMessage(error));
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
  }, [activeLoadQuery, botSearchMode, executeLoad, resource]);

  useEffect(
    () => () => {
      latestListRequest.current += 1;
      loadMoreController.current?.abort();
      loadingMoreRef.current = false;
      useCollaborationSquareStore.getState().reset();
    },
    [],
  );

  return { load, loadMore, hasMore, hasMoreRef, loadMoreRef, loadingMore, loadMoreError };
}
