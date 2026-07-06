/**
 * useActor - Actor (Bot) 管理业务逻辑 Hook
 * 替换原有的 discoverBots 功能
 */

import type {
  ActorListResponse,
  ActorSearchResponse,
} from '@/services/backend-api/ActorController';
import * as ActorController from '@/services/backend-api/ActorController';
import { useActorStore, type ActorBot } from '@/stores/actorStore';
import { handleNetworkError } from '@/utils/hooksErrorHandler';
import { useCallback, useRef } from 'react';

export interface LoadActorsParams {
  currentBotUuid: string;
  cooperatableOnly?: boolean;
  pageNo?: number;
  pageSize?: number;
  name?: string;
  append?: boolean;
}

export interface SearchActorsParams {
  currentBotUuid: string;
  cooperatableOnly?: boolean;
  q: string;
}

/**
 * Actor (Bot) 管理 Hook
 * @returns Actor 列表状态和管理方法
 */
export function useActor() {
  const {
    actors,
    isLoading,
    hasLoaded,
    pagination,
    searchQuery,
    cooperatableOnly,
    setActors,
    setLoading,
    setHasLoaded,
    setPagination,
    setSearchQuery,
    setCooperatableOnly,
    reset,
    updateActor,
  } = useActorStore();

  // 防止重复请求的 ref
  const loadingRef = useRef(false);

  /**
   * 加载 Actor 列表（用于添加好友、公开 Bot 搜索）
   * /api/actors/list?current_bot_uuid=xxx&cooperatable_only=false&page_no=1&page_size=20&name=xxx
   */
  const loadActors = useCallback(
    async (params: LoadActorsParams): Promise<ActorListResponse> => {
      // 防止并发请求
      if (loadingRef.current) {
        console.log('[useActor] Request skipped, already loading');
        return { bots: [], total: 0 };
      }

      const {
        currentBotUuid,
        cooperatableOnly: cooperatorParam,
        pageNo = 1,
        pageSize = 20,
        name,
        append = false,
      } = params;

      try {
        loadingRef.current = true;
        setLoading(true);

        const response = await ActorController.getActorList({
          current_bot_uuid: currentBotUuid,
          cooperatable_only: cooperatorParam ?? cooperatableOnly,
          page_no: pageNo,
          page_size: pageSize,
          name,
        });

        if (response.bots) {
          setActors(response.bots, response.total, append);
          setHasLoaded(true);
        }
        return response;
      } catch (error: any) {
        console.error('[useActor] Failed to load actors:', error);
        handleNetworkError(error, {
          module: 'Actor',
          action: '加载 Actor 列表',
        });
        return { bots: [], total: 0 };
      } finally {
        setLoading(false);
        loadingRef.current = false;
      }
    },
    [cooperatableOnly, setActors, setHasLoaded, setLoading],
  );

  /**
   * 搜索 Actor（智能搜索，用于拉起协作-智能搜索）
   * /api/actors/search?current_bot_uuid=xxx&cooperatable_only=true&q=xxx
   */
  const searchActors = useCallback(
    async (params: SearchActorsParams): Promise<ActorSearchResponse> => {
      // 防止并发请求
      if (loadingRef.current) {
        console.log('[useActor] Search skipped, already loading');
        return { bots: [], context: { recommend_response: null } };
      }

      const { currentBotUuid, cooperatableOnly: cooperatorParam, q } = params;

      try {
        loadingRef.current = true;
        setLoading(true);
        setSearchQuery(q);

        const response = await ActorController.searchActors({
          current_bot_uuid: currentBotUuid,
          cooperatable_only: cooperatorParam ?? cooperatableOnly,
          q,
        });

        if (response.bots) {
          // 搜索不走分页，直接覆盖
          setActors(response.bots, response.bots.length, false);
          setHasLoaded(true);
        }
        return response;
      } catch (error: any) {
        console.error('[useActor] Failed to search actors:', error);
        handleNetworkError(error, {
          module: 'Actor',
          action: '搜索 Actor',
        });
        return { bots: [], context: { recommend_response: null } };
      } finally {
        setLoading(false);
        loadingRef.current = false;
      }
    },
    [cooperatableOnly, setActors, setHasLoaded, setLoading, setSearchQuery],
  );

  /**
   * 加载更多 Actor（分页）
   */
  const loadMoreActors = useCallback(
    async (currentBotUuid: string): Promise<ActorBot[]> => {
      const nextPageNo = pagination.page_no + 1;
      const totalPages = Math.ceil(pagination.total / pagination.page_size);

      if (nextPageNo > totalPages) {
        console.log('[useActor] No more actors to load');
        return [];
      }

      return loadActors({
        currentBotUuid,
        pageNo: nextPageNo,
        append: true,
      });
    },
    [pagination, loadActors],
  );

  /**
   * 重置状态
   */
  const resetActors = useCallback(() => {
    reset();
  }, [reset]);

  /**
   * 更新 Actor 好友状态
   */
  const setActorFriendStatus = useCallback(
    (botUuid: string, isFriend: boolean) => {
      updateActor(botUuid, { is_friend: isFriend });
    },
    [updateActor],
  );

  return {
    // State
    actors,
    isLoading,
    hasLoaded,
    pagination,
    searchQuery,
    cooperatableOnly,

    // Actions
    loadActors,
    searchActors,
    loadMoreActors,
    resetActors,
    setActorFriendStatus,
    setCooperatableOnly,
    setSearchQuery,
    setPagination,
  };
}
