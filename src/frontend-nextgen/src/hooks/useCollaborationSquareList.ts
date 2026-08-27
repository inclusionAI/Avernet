import type {
  BotSearchMode,
  HumanBotActionContext,
  PublicBot,
  PublicGroup,
  SquareResource,
} from '@/domain/collaborationSquare/types';
import type { HumanIdentityStatus } from '@/hooks/useHumanIdentity';
import { collaborationSquareBotService, collaborationSquareGroupService } from '@/services/collaborationSquare';
import type { CollaborationSquareState } from '@/stores/collaborationSquareStore';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { useCallback, useEffect, useRef } from 'react';

interface UseCollaborationSquareListOptions {
  resource: SquareResource;
  humanBotContext: HumanBotActionContext | null;
  humanIdentityStatus: HumanIdentityStatus;
  botQuery: string;
  groupQuery: string;
  botSearchMode: BotSearchMode;
  setBots: CollaborationSquareState['setBots'];
  setGroups: CollaborationSquareState['setGroups'];
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
  setBots,
  setGroups,
  setLoading,
  setError,
}: UseCollaborationSquareListOptions) {
  const latestListRequest = useRef(0);

  const executeLoad = useCallback(
    async (requestId: number, query: string, mode: BotSearchMode, signal?: AbortSignal) => {
      if (resource === 'bot' && !humanBotContext) {
        setLoading(humanIdentityStatus === 'loading');
        setError(humanIdentityStatus === 'loading' ? null : '当前用户身份不可用，请刷新后重试');
        return;
      }
      setLoading(true);
      setError(null);
      try {
        if (resource === 'bot') {
          const keyword = query.trim();
          const bots: PublicBot[] =
            mode === 'smart' && keyword
              ? await collaborationSquareBotService.discoverBots(
                  { keyword, topK: 20, minScore: 0.1, runtimeState: 'online' },
                  humanBotContext ?? undefined,
                  signal,
                )
              : await collaborationSquareBotService.listBots(
                  { ...(mode === 'name' && keyword ? { search: keyword } : {}), page: 1, pageSize: 20 },
                  humanBotContext ?? undefined,
                  signal,
                );
          if (requestId === latestListRequest.current && !signal?.aborted) setBots(bots);
        } else {
          const search = query.trim();
          const groups: PublicGroup[] = await collaborationSquareGroupService.listGroups(
            { ...(search ? { search } : {}), offset: 0, limit: 20 },
            signal,
          );
          if (requestId === latestListRequest.current && !signal?.aborted) setGroups(groups);
        }
      } catch (error) {
        if (requestId === latestListRequest.current && (error as Error).name !== 'AbortError') {
          setError(errorMessage(error));
        }
      } finally {
        if (requestId === latestListRequest.current) setLoading(false);
      }
    },
    [humanBotContext, humanIdentityStatus, resource, setBots, setError, setGroups, setLoading],
  );

  const load = useCallback(
    (signal?: AbortSignal) => {
      const requestId = ++latestListRequest.current;
      return executeLoad(requestId, botQuery, botSearchMode, signal);
    },
    [botQuery, botSearchMode, executeLoad],
  );

  const activeLoadQuery = resource === 'bot' ? botQuery : groupQuery;

  useEffect(() => {
    const controller = new AbortController();
    const requestId = ++latestListRequest.current;
    const delay = activeLoadQuery.trim() ? 300 : 0;
    const timer = setTimeout(() => {
      void executeLoad(requestId, activeLoadQuery, botSearchMode, controller.signal);
    }, delay);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [activeLoadQuery, botSearchMode, executeLoad, resource]);

  useEffect(
    () => () => {
      latestListRequest.current += 1;
      useCollaborationSquareStore.getState().reset();
    },
    [],
  );

  return load;
}
