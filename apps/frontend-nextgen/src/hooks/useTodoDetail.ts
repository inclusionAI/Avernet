import { loadTodo, loadTodoInteractions } from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import { safeReportError } from '@/utils/errorToast';
import { useCallback, useEffect, useRef } from 'react';
import { useTodoDetailActions } from './useTodoDetailActions';
import { useTodoDetailResources } from './useTodoDetailResources';
import { useTodoMentionBots } from './useTodoMentionBots';
import { useTodoProductRuntime } from './useTodoProductRuntime';

const RUNNING_POLL_INTERVAL_MS = 5_000;

function sortEvents<T extends { eventSeq: number }>(events: T[]): T[] {
  return [...events].sort((left, right) => left.eventSeq - right.eventSeq);
}

export function useTodoDetail(todoId: string | null, onListRefresh?: () => Promise<void> | void) {
  const storedSelectedTodo = useTodoStore((state) => state.selectedTodo);
  const storedInteractions = useTodoStore((state) => state.interactions);
  const detailLoading = useTodoStore((state) => state.detailLoading);
  const mutationLoading = useTodoStore((state) => state.mutationLoading);
  const storedError = useTodoStore((state) => state.detailError);
  const setSelectedTodo = useTodoStore((state) => state.setSelectedTodo);
  const setInteractions = useTodoStore((state) => state.setInteractions);
  const setLoading = useTodoStore((state) => state.setDetailLoading);
  const setError = useTodoStore((state) => state.setDetailError);
  const resetDetail = useTodoStore((state) => state.resetDetail);
  const requestRef = useRef(0);
  const loadedTodoIdRef = useRef<string | null>(null);

  // Store 是全局详情缓存；换 ID 时先按当前 ID 派生空数据，避免路由先渲染上一个 Todo。
  const selectedTodo = storedSelectedTodo?.todoId === todoId ? storedSelectedTodo : null;
  const hasCurrentDetail = selectedTodo !== null;
  const interactions = hasCurrentDetail ? storedInteractions : [];
  const error =
    hasCurrentDetail || detailLoading || loadedTodoIdRef.current === todoId ? storedError : null;
  const loading = detailLoading || Boolean(todoId && !hasCurrentDetail && !error);
  const mentionBots = useTodoMentionBots(selectedTodo?.spaceId);
  const resources = useTodoDetailResources(todoId, hasCurrentDetail);
  const productRuntime = useTodoProductRuntime({
    todoId,
    selectedTodo,
    hasCurrentDetail,
    onListRefresh,
  });

  const refresh = useCallback(async () => {
    if (!todoId) {
      requestRef.current += 1;
      resetDetail();
      return;
    }
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    void resources.refreshEvents();
    void resources.refreshArtifacts();
    void productRuntime.refreshProgress();
    try {
      const [todo, todoInteractions] = await Promise.all([
        loadTodo(todoId),
        loadTodoInteractions(todoId),
      ]);
      if (requestId !== requestRef.current) return;
      setSelectedTodo(todo);
      setInteractions(sortEvents(todoInteractions));
    } catch (cause) {
      if (requestId !== requestRef.current) return;
      setError(cause instanceof Error ? cause.message : '待办详情加载失败');
      safeReportError(cause, { title: '待办详情加载失败' });
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, [
    productRuntime.refreshProgress,
    resetDetail,
    resources.refreshArtifacts,
    resources.refreshEvents,
    setError,
    setInteractions,
    setLoading,
    setSelectedTodo,
    todoId,
  ]);

  const actions = useTodoDetailActions({ todoId, selectedTodo, refresh, onListRefresh });

  useEffect(() => {
    if (loadedTodoIdRef.current !== todoId) {
      loadedTodoIdRef.current = todoId;
      resetDetail();
    }
    void refresh();
    return () => {
      requestRef.current += 1;
    };
  }, [refresh, resetDetail, todoId]);

  useEffect(() => {
    if (!todoId || selectedTodo?.status !== 'in_progress') return;
    const timer = setInterval(() => {
      void refresh();
    }, RUNNING_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh, selectedTodo?.status, todoId]);

  return {
    selectedTodo,
    interactions,
    events: resources.events,
    artifacts: resources.artifacts,
    loading,
    eventsLoading: resources.eventsLoading,
    artifactsLoading: resources.artifactsLoading,
    mutationLoading,
    error,
    eventsError: resources.eventsError,
    artifactsError: resources.artifactsError,
    progress: productRuntime.progress,
    progressLoading: productRuntime.progressLoading,
    progressError: productRuntime.progressError,
    productInteractionPage: productRuntime.productInteractionPage,
    productInteractionLoading: productRuntime.productInteractionLoading,
    productInteractionError: productRuntime.productInteractionError,
    isProductInteractionRuntime: productRuntime.isProductInteractionRuntime,
    mentionBots: mentionBots.bots,
    mentionBotsLoading: mentionBots.loading,
    mentionBotsError: mentionBots.error,
    refresh,
    refreshEvents: resources.refreshEvents,
    refreshArtifacts: resources.refreshArtifacts,
    refreshProgress: productRuntime.refreshProgress,
    refreshProductInteractions: productRuntime.refreshProductInteractions,
    readArtifactContent: resources.readArtifactContent,
    interact: actions.interact,
    productInteract: productRuntime.productInteract,
    productStopInteraction: productRuntime.productStopInteraction,
    accept: actions.accept,
  };
}
