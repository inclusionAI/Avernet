import type {
  PublicTask,
  PublicTaskSearchQuery,
  SquareResource,
  TaskStatusFilter,
} from '@/domain/collaborationSquare/types';
import { COLLABORATION_SQUARE_PAGE_SIZE } from '@/hooks/useCollaborationSquareList';
import { collaborationSquareTaskService } from '@/services/collaborationSquare';
import { useCollaborationSquareStore } from '@/stores/collaborationSquareStore';
import { getCollaborationSquareErrorMessage } from '@/utils/collaborationSquare';
import { useCallback, useEffect, useRef, useState } from 'react';

const TASK_SEARCH_DEBOUNCE_MS = 1_000;
const TASK_PAGE_SIZE = COLLABORATION_SQUARE_PAGE_SIZE;

/** 组装任务广场搜索/状态筛选/分页入参；`status='all'` 与空 search 不下发字段，交给 adapter 全量返回。 */
function buildTaskQuery(search: string, status: TaskStatusFilter, offset: number): PublicTaskSearchQuery {
  return {
    ...(search ? { search } : {}),
    ...(status !== 'all' ? { status } : {}),
    offset,
    limit: TASK_PAGE_SIZE,
  };
}

export interface CollaborationSquareTaskListView {
  load: (signal?: AbortSignal) => Promise<void>;
  loadMore: () => Promise<void>;
  hasMore: boolean;
  loadingMore: boolean;
  loadMoreError: string | null;
}

export interface UseCollaborationSquareTaskResult {
  list: CollaborationSquareTaskListView;
  openTaskDetail: (task: PublicTask) => void;
  closeTaskDetail: () => void;
}

/**
 * 任务广场只读加载与详情编排。仅 resource='task' 时活跃，其余资源 no-op（交由 useCollaborationSquareList）。
 * 跨用户公开 BBS 接力求助任务，不做 owner 过滤；search/status 由 adapter 过滤。详情为纯只读弹层，不接深链。
 */
export function useCollaborationSquareTask(resource: SquareResource): UseCollaborationSquareTaskResult {
  const { taskQuery, taskStatusFilter, setTasks, appendTasks, setLoading, setError, setSelectedTaskId, setTaskDetail } =
    useCollaborationSquareStore();
  const latestRequest = useRef(0);
  const currentOffset = useRef(0);
  const loadMoreController = useRef<AbortController | null>(null);
  const loadingMoreRef = useRef(false);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  const buildQuery = useCallback(
    (offset: number) => buildTaskQuery(taskQuery.trim(), taskStatusFilter, offset),
    [taskQuery, taskStatusFilter],
  );

  const executeLoad = useCallback(
    async (requestId: number, signal?: AbortSignal) => {
      loadMoreController.current?.abort();
      loadingMoreRef.current = false;
      setLoadingMore(false);
      setLoadMoreError(null);
      currentOffset.current = 0;
      setLoading(true);
      setError(null);
      try {
        const page = await collaborationSquareTaskService.listPublicTasks(buildQuery(0), signal);
        if (requestId === latestRequest.current && !signal?.aborted) {
          setTasks(page.items);
          currentOffset.current = TASK_PAGE_SIZE;
          setHasMore(currentOffset.current < page.total);
        }
      } catch (error) {
        // list target_invalid 语义为「整页失效」，交 UI error 态统一展示，不在列表层特判删除任务。
        if (requestId === latestRequest.current && (error as Error).name !== 'AbortError') {
          setHasMore(false);
          setError(getCollaborationSquareErrorMessage(error));
        }
      } finally {
        if (requestId === latestRequest.current) setLoading(false);
      }
    },
    [buildQuery, setError, setLoading, setTasks],
  );

  const load = useCallback(
    (signal?: AbortSignal) => {
      const requestId = ++latestRequest.current;
      return executeLoad(requestId, signal);
    },
    [executeLoad],
  );

  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || hasMore === false) return;
    const requestId = latestRequest.current;
    const controller = new AbortController();
    loadMoreController.current?.abort();
    loadMoreController.current = controller;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const offset = currentOffset.current;
      const result = await collaborationSquareTaskService.listPublicTasks(buildQuery(offset), controller.signal);
      if (requestId !== latestRequest.current || controller.signal.aborted) return;
      appendTasks(result.items);
      currentOffset.current = offset + TASK_PAGE_SIZE;
      setHasMore(currentOffset.current < result.total);
    } catch (error) {
      if (requestId === latestRequest.current && (error as Error).name !== 'AbortError') {
        setLoadMoreError(getCollaborationSquareErrorMessage(error));
      }
    } finally {
      if (requestId === latestRequest.current) {
        loadingMoreRef.current = false;
        setLoadingMore(false);
        if (loadMoreController.current === controller) loadMoreController.current = null;
      }
    }
  }, [appendTasks, buildQuery, hasMore]);

  useEffect(() => {
    if (resource !== 'task') return;
    const controller = new AbortController();
    const requestId = ++latestRequest.current;
    const delay = taskQuery.trim() ? TASK_SEARCH_DEBOUNCE_MS : 0;
    const timer = setTimeout(() => {
      void executeLoad(requestId, controller.signal);
    }, delay);
    return () => {
      clearTimeout(timer);
      controller.abort();
      loadMoreController.current?.abort();
    };
  }, [executeLoad, resource, taskQuery]);

  const closeTaskDetail = useCallback(() => {
    setSelectedTaskId(null);
    setTaskDetail(null);
  }, [setSelectedTaskId, setTaskDetail]);

  // 详情直接用已加载的列表项填充（来自内存），不发请求，无失效路径：详情数据与列表同源，
  // 真实 BBS 端点不提供单点详情，故不存在 target_invalid/网络失败的 toast 编排。
  const openTaskDetail = useCallback(
    (task: PublicTask) => {
      setSelectedTaskId(task.id);
      setTaskDetail(task);
    },
    [setSelectedTaskId, setTaskDetail],
  );

  return { list: { load, loadMore, hasMore, loadingMore, loadMoreError }, openTaskDetail, closeTaskDetail };
}
