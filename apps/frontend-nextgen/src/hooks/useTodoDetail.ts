import { notifySuccess } from '@/components/ui/notify';
import type { AcceptanceVerdict } from '@/domain/todos/models';
import { canInteractWithTodo } from '@/domain/todos/status';
import {
  acceptTodo,
  addTodoInteraction,
  downloadTodoArtifactContent,
  loadTodo,
  loadTodoArtifacts,
  loadTodoEvents,
  loadTodoInteractions,
} from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import { safeReportError } from '@/utils/errorToast';
import { useCallback, useEffect, useRef } from 'react';

const RUNNING_POLL_INTERVAL_MS = 5_000;

function sortEvents<T extends { eventSeq: number }>(events: T[]): T[] {
  return [...events].sort((left, right) => left.eventSeq - right.eventSeq);
}

export function useTodoDetail(todoId: string | null, onListRefresh?: () => Promise<void> | void) {
  const selectedTodo = useTodoStore((state) => state.selectedTodo);
  const interactions = useTodoStore((state) => state.interactions);
  const events = useTodoStore((state) => state.events);
  const artifacts = useTodoStore((state) => state.artifacts);
  const loading = useTodoStore((state) => state.detailLoading);
  const eventsLoading = useTodoStore((state) => state.eventsLoading);
  const artifactsLoading = useTodoStore((state) => state.artifactsLoading);
  const mutationLoading = useTodoStore((state) => state.mutationLoading);
  const error = useTodoStore((state) => state.detailError);
  const eventsError = useTodoStore((state) => state.eventsError);
  const artifactsError = useTodoStore((state) => state.artifactsError);
  const setSelectedTodo = useTodoStore((state) => state.setSelectedTodo);
  const setInteractions = useTodoStore((state) => state.setInteractions);
  const setEvents = useTodoStore((state) => state.setEvents);
  const setArtifacts = useTodoStore((state) => state.setArtifacts);
  const setLoading = useTodoStore((state) => state.setDetailLoading);
  const setEventsLoading = useTodoStore((state) => state.setEventsLoading);
  const setArtifactsLoading = useTodoStore((state) => state.setArtifactsLoading);
  const setMutationLoading = useTodoStore((state) => state.setMutationLoading);
  const setError = useTodoStore((state) => state.setDetailError);
  const setEventsError = useTodoStore((state) => state.setEventsError);
  const setArtifactsError = useTodoStore((state) => state.setArtifactsError);
  const resetDetail = useTodoStore((state) => state.resetDetail);
  const requestRef = useRef(0);
  const eventRequestRef = useRef(0);
  const artifactRequestRef = useRef(0);

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

  const refresh = useCallback(async () => {
    if (!todoId) {
      requestRef.current += 1;
      eventRequestRef.current += 1;
      artifactRequestRef.current += 1;
      resetDetail();
      return;
    }
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    void refreshEvents();
    void refreshArtifacts();
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
  }, [refreshArtifacts, refreshEvents, resetDetail, setError, setInteractions, setLoading, setSelectedTodo, todoId]);

  useEffect(() => {
    void refresh();
    return () => {
      requestRef.current += 1;
      eventRequestRef.current += 1;
      artifactRequestRef.current += 1;
    };
  }, [refresh]);

  useEffect(() => {
    if (!todoId || selectedTodo?.status !== 'in_progress') return;
    const timer = setInterval(() => {
      void refresh();
    }, RUNNING_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh, selectedTodo?.status, todoId]);

  const interact = useCallback(
    async (message: string) => {
      if (!todoId || !selectedTodo || !canInteractWithTodo(selectedTodo.status)) {
        safeReportError(new Error('当前状态不允许互动'), { title: '互动失败' });
        return false;
      }
      const text = message.trim();
      if (!text) {
        safeReportError(new Error('请输入互动内容'), { title: '互动失败' });
        return false;
      }
      setMutationLoading(true);
      try {
        await addTodoInteraction(todoId, { message: text });
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
    async (verdict: AcceptanceVerdict, message?: string) => {
      if (!todoId || !selectedTodo || selectedTodo.status !== 'pending_acceptance') {
        safeReportError(new Error('当前状态不允许验收'), { title: '验收失败' });
        return false;
      }
      setMutationLoading(true);
      try {
        await acceptTodo(todoId, {
          verdict,
          message: message?.trim() || undefined,
        });
        notifySuccess(verdict === 'approved' ? '验收已通过' : '已记录验收意见');
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
    refresh,
    refreshEvents,
    refreshArtifacts,
    readArtifactContent,
    interact,
    accept,
  };
}
