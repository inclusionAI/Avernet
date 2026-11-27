import { notifySuccess } from '@/components/ui/notify';
import type { CreateTodoInteractionInput, TodoAcceptanceInput } from '@/domain/todos/models';
import { canInteractWithTodo } from '@/domain/todos/status';
import {
  acceptTodo,
  addTodoInteraction,
  downloadTodoArtifactContent,
  loadTodo,
  loadTodoArtifacts,
  loadTodoEvents,
  loadTodoInteractions,
  loadTodoProgress,
} from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import { safeReportError } from '@/utils/errorToast';
import { useCallback, useEffect, useRef } from 'react';
import { useTodoMentionBots } from './useTodoMentionBots';

const RUNNING_POLL_INTERVAL_MS = 5_000;

function sortEvents<T extends { eventSeq: number }>(events: T[]): T[] {
  return [...events].sort((left, right) => left.eventSeq - right.eventSeq);
}

export function useTodoDetail(todoId: string | null, onListRefresh?: () => Promise<void> | void) {
  const storedSelectedTodo = useTodoStore((state) => state.selectedTodo);
  const storedInteractions = useTodoStore((state) => state.interactions);
  const storedEvents = useTodoStore((state) => state.events);
  const storedArtifacts = useTodoStore((state) => state.artifacts);
  const storedProgress = useTodoStore((state) => state.progress);
  const detailLoading = useTodoStore((state) => state.detailLoading);
  const eventsLoading = useTodoStore((state) => state.eventsLoading);
  const artifactsLoading = useTodoStore((state) => state.artifactsLoading);
  const progressLoading = useTodoStore((state) => state.progressLoading);
  const mutationLoading = useTodoStore((state) => state.mutationLoading);
  const storedError = useTodoStore((state) => state.detailError);
  const eventsError = useTodoStore((state) => state.eventsError);
  const artifactsError = useTodoStore((state) => state.artifactsError);
  const progressError = useTodoStore((state) => state.progressError);
  const setSelectedTodo = useTodoStore((state) => state.setSelectedTodo);
  const setInteractions = useTodoStore((state) => state.setInteractions);
  const setEvents = useTodoStore((state) => state.setEvents);
  const setArtifacts = useTodoStore((state) => state.setArtifacts);
  const setProgress = useTodoStore((state) => state.setProgress);
  const setLoading = useTodoStore((state) => state.setDetailLoading);
  const setEventsLoading = useTodoStore((state) => state.setEventsLoading);
  const setArtifactsLoading = useTodoStore((state) => state.setArtifactsLoading);
  const setProgressLoading = useTodoStore((state) => state.setProgressLoading);
  const setMutationLoading = useTodoStore((state) => state.setMutationLoading);
  const setError = useTodoStore((state) => state.setDetailError);
  const setEventsError = useTodoStore((state) => state.setEventsError);
  const setArtifactsError = useTodoStore((state) => state.setArtifactsError);
  const setProgressError = useTodoStore((state) => state.setProgressError);
  const resetDetail = useTodoStore((state) => state.resetDetail);
  const requestRef = useRef(0);
  const eventRequestRef = useRef(0);
  const artifactRequestRef = useRef(0);
  const progressRequestRef = useRef(0);
  const loadedTodoIdRef = useRef<string | null>(null);

  // Store 是全局详情缓存；换 ID 时先按当前 ID 派生空数据，避免路由先渲染上一个 Todo。
  const selectedTodo = storedSelectedTodo?.todoId === todoId ? storedSelectedTodo : null;
  const mentionBots = useTodoMentionBots(selectedTodo?.spaceId);
  const hasCurrentDetail = selectedTodo !== null;
  const interactions = hasCurrentDetail ? storedInteractions : [];
  const events = hasCurrentDetail ? storedEvents : [];
  const artifacts = hasCurrentDetail ? storedArtifacts.filter((item) => item.todoId === todoId) : [];
  const progress = hasCurrentDetail && storedProgress?.todoId === todoId ? storedProgress : null;
  const error =
    hasCurrentDetail || detailLoading || loadedTodoIdRef.current === todoId ? storedError : null;
  const loading = detailLoading || Boolean(todoId && !hasCurrentDetail && !error);

  const refreshEvents = useCallback(async () => {
    if (!todoId) return;
    const requestId = ++eventRequestRef.current;
    setEventsLoading(true);
    setEventsError(null);
    try {
      const todoEvents = await loadTodoEvents(todoId);
      if (requestId === eventRequestRef.current) setEvents(sortEvents(todoEvents));
    } catch (cause) {
      if (requestId !== eventRequestRef.current) return;
      setEvents([]);
      setEventsError(cause instanceof Error ? cause.message : '事件时序加载失败');
    } finally {
      if (requestId === eventRequestRef.current) setEventsLoading(false);
    }
  }, [setEvents, setEventsError, setEventsLoading, todoId]);

  const refreshArtifacts = useCallback(async () => {
    if (!todoId) return;
    const requestId = ++artifactRequestRef.current;
    setArtifactsLoading(true);
    setArtifactsError(null);
    try {
      const todoArtifacts = await loadTodoArtifacts(todoId);
      if (requestId === artifactRequestRef.current) setArtifacts(todoArtifacts);
    } catch (cause) {
      if (requestId !== artifactRequestRef.current) return;
      setArtifacts([]);
      setArtifactsError(cause instanceof Error ? cause.message : '产物加载失败');
    } finally {
      if (requestId === artifactRequestRef.current) setArtifactsLoading(false);
    }
  }, [setArtifacts, setArtifactsError, setArtifactsLoading, todoId]);

  const refreshProgress = useCallback(
    async (expandedStageCode?: string) => {
      if (!todoId) return;
      const requestId = ++progressRequestRef.current;
      setProgressLoading(true);
      setProgressError(null);
      try {
        const todoProgress = await loadTodoProgress(todoId, {
          expandedStageCode,
          messageLimit: expandedStageCode ? 200 : 50,
        });
        if (requestId === progressRequestRef.current) setProgress(todoProgress);
      } catch (cause) {
        if (requestId !== progressRequestRef.current) return;
        setProgress(null);
        setProgressError(cause instanceof Error ? cause.message : '执行进度加载失败');
      } finally {
        if (requestId === progressRequestRef.current) setProgressLoading(false);
      }
    },
    [setProgress, setProgressError, setProgressLoading, todoId],
  );

  const refresh = useCallback(async () => {
    if (!todoId) {
      requestRef.current += 1;
      eventRequestRef.current += 1;
      artifactRequestRef.current += 1;
      progressRequestRef.current += 1;
      resetDetail();
      return;
    }
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    void refreshEvents();
    void refreshArtifacts();
    void refreshProgress();
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
  }, [refreshArtifacts, refreshEvents, refreshProgress, resetDetail, setError, setInteractions, setLoading, setSelectedTodo, todoId]);

  useEffect(() => {
    if (loadedTodoIdRef.current !== todoId) {
      loadedTodoIdRef.current = todoId;
      resetDetail();
    }
    void refresh();
    return () => {
      requestRef.current += 1;
      eventRequestRef.current += 1;
      artifactRequestRef.current += 1;
      progressRequestRef.current += 1;
    };
  }, [refresh, resetDetail, todoId]);

  useEffect(() => {
    if (!todoId || selectedTodo?.status !== 'in_progress') return;
    const timer = setInterval(() => {
      void refresh();
    }, RUNNING_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh, selectedTodo?.status, todoId]);

  const interact = useCallback(
    async (input: CreateTodoInteractionInput) => {
      if (!todoId || !selectedTodo || !canInteractWithTodo(selectedTodo.status)) {
        safeReportError(new Error('当前状态不允许互动'), { title: '互动失败' });
        return false;
      }
      const text = input.message.trim();
      if (!text) {
        safeReportError(new Error('请输入互动内容'), { title: '互动失败' });
        return false;
      }
      setMutationLoading(true);
      try {
        await addTodoInteraction(todoId, { ...input, message: text });
        notifySuccess('互动已提交');
        await refresh();
        await onListRefresh?.();
        return true;
      } catch (cause) {
        safeReportError(cause, { title: '提交互动失败' });
        return false;
      } finally {
        setMutationLoading(false);
      }
    },
    [onListRefresh, refresh, selectedTodo, setMutationLoading, todoId],
  );

  const accept = useCallback(
    async (input: TodoAcceptanceInput) => {
      if (!todoId || !selectedTodo || selectedTodo.status !== 'pending_action') {
        safeReportError(new Error('当前状态不允许验收'), { title: '验收失败' });
        return false;
      }
      const message = input.message?.trim() || undefined;
      if (input.verdict === 'rejected' && (!message || input.targets.length === 0)) {
        safeReportError(new Error('请标记问题楼层并填写返工要求'), { title: '验收失败' });
        return false;
      }
      setMutationLoading(true);
      try {
        await acceptTodo(todoId, { ...input, message });
        notifySuccess(input.verdict === 'approved' ? '验收已通过' : '已退回 Bot 返工');
        await refresh();
        await onListRefresh?.();
        return true;
      } catch (cause) {
        safeReportError(cause, { title: '提交验收失败' });
        return false;
      } finally {
        setMutationLoading(false);
      }
    },
    [onListRefresh, refresh, selectedTodo, setMutationLoading, todoId],
  );

  const readArtifactContent = useCallback(
    async (artifactId: string, disposition: 'inline' | 'attachment') => {
      if (!todoId) throw new Error('待办不存在');
      return downloadTodoArtifactContent(todoId, artifactId, disposition);
    },
    [todoId],
  );

  return {
    selectedTodo,
    interactions,
    events,
    artifacts,
    loading,
    eventsLoading,
    artifactsLoading,
    mutationLoading,
    error,
    eventsError,
    artifactsError,
    progress,
    progressLoading,
    progressError,
    mentionBots: mentionBots.bots,
    mentionBotsLoading: mentionBots.loading,
    mentionBotsError: mentionBots.error,
    refresh,
    refreshEvents,
    refreshArtifacts,
    refreshProgress,
    readArtifactContent,
    interact,
    accept,
  };
}
