import type {
  BotCatalogViewer,
  BotSearchMode,
  HumanBotActionContext,
  PublicBot,
} from '@/domain/collaborationSquare/types';
import { collaborationSquareBotService } from '@/services/collaborationSquare';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const PAGE_SIZE = 24;
const DEBOUNCE_MS = 300;

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}

interface UseBotCatalogFetchOptions {
  viewer: BotCatalogViewer | null;
  humanBotContext: HumanBotActionContext | null;
  enabled: boolean;
  pageSize?: number;
}

/**
 * 公开 Bot 面板的读路径 Hook：本地 state 管理 bots/query/mode 与首屏/分页/防抖，
 * 并带 viewer 身份下发 bot-catalog。智能搜索空关键词不发请求（交由面板展示提示）。
 * 从 {@link usePublicBotCatalog} 拆出以满足 Hook≤250 行守卫。
 */
export function useBotCatalogFetch({
  viewer,
  humanBotContext,
  enabled,
  pageSize = PAGE_SIZE,
}: UseBotCatalogFetchOptions) {
  const [bots, setBots] = useState<PublicBot[]>([]);
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<BotSearchMode>('name');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  const latestRequest = useRef(0);
  const currentPage = useRef(1);
  const loadMoreController = useRef<AbortController | null>(null);
  const viewerFields = useMemo(
    () => (viewer ? { viewerActorType: viewer.viewerActorType, viewerActorId: viewer.viewerActorId } : {}),
    [viewer],
  );

  const executeLoad = useCallback(
    async (requestId: number, q: string, m: BotSearchMode, signal?: AbortSignal) => {
      loadMoreController.current?.abort();
      setLoadingMore(false);
      setLoadMoreError(null);
      currentPage.current = 1;
      const keyword = q.trim();
      if (m === 'smart' && !keyword) {
        setBots([]);
        setHasMore(false);
        setLoading(false);
        setError(null);
        return;
      }
      if (!humanBotContext) {
        setHasMore(false);
        setLoading(false);
        setError('当前查看身份不可用，请刷新后重试');
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const result: PublicBot[] =
          m === 'smart' && keyword
            ? await collaborationSquareBotService.discoverBots(
                { keyword, topK: 20, minScore: 0.1, runtimeState: 'online', ...viewerFields },
                humanBotContext,
                signal,
              )
            : await collaborationSquareBotService.listBots(
                { ...(m === 'name' && keyword ? { search: keyword } : {}), page: 1, pageSize, ...viewerFields },
                humanBotContext,
                signal,
              );
        if (requestId === latestRequest.current && !signal?.aborted) {
          setBots(result);
          setHasMore(!(m === 'smart' && keyword) && result.length >= pageSize);
        }
      } catch (e) {
        if (requestId === latestRequest.current && (e as Error).name !== 'AbortError') {
          setHasMore(false);
          setError(errorMessage(e));
        }
      } finally {
        if (requestId === latestRequest.current) setLoading(false);
      }
    },
    [humanBotContext, pageSize, viewerFields],
  );

  // 打开时重置搜索，避免上次输入残留
  useEffect(() => {
    if (enabled) {
      setQuery('');
      setMode('name');
      setBots([]);
    }
  }, [enabled]);

  // 首屏 + 搜索/模式切换（防抖）
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    const requestId = ++latestRequest.current;
    const delay = query.trim() ? DEBOUNCE_MS : 0;
    const timer = setTimeout(() => {
      void executeLoad(requestId, query, mode, controller.signal);
    }, delay);
    return () => {
      clearTimeout(timer);
      controller.abort();
      loadMoreController.current?.abort();
    };
  }, [enabled, query, mode, executeLoad]);

  const loadMore = useCallback(async () => {
    if (loadingMore || hasMore === false) return;
    if (mode !== 'name' || !humanBotContext) return;
    const requestId = latestRequest.current;
    const controller = new AbortController();
    loadMoreController.current?.abort();
    loadMoreController.current = controller;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const page = currentPage.current + 1;
      const keyword = query.trim();
      const result = await collaborationSquareBotService.listBots(
        { ...(keyword ? { search: keyword } : {}), page, pageSize, ...viewerFields },
        humanBotContext,
        controller.signal,
      );
      if (requestId !== latestRequest.current || controller.signal.aborted) return;
      setBots((cur) => [...cur, ...result.filter((bot) => !cur.some((item) => item.id === bot.id))]);
      currentPage.current = page;
      setHasMore(result.length >= pageSize);
    } catch (e) {
      if (requestId === latestRequest.current && (e as Error).name !== 'AbortError') setLoadMoreError(errorMessage(e));
    } finally {
      if (requestId === latestRequest.current) {
        setLoadingMore(false);
        if (loadMoreController.current === controller) loadMoreController.current = null;
      }
    }
  }, [hasMore, humanBotContext, loadingMore, mode, pageSize, query, viewerFields]);

  const reload = useCallback(() => {
    const requestId = ++latestRequest.current;
    const controller = new AbortController();
    void executeLoad(requestId, query, mode, controller.signal);
  }, [executeLoad, query, mode]);

  return {
    bots,
    setBots,
    query,
    setQuery,
    mode,
    setMode,
    loading,
    error,
    hasMore,
    loadingMore,
    loadMoreError,
    reload,
    loadMore,
  };
}
