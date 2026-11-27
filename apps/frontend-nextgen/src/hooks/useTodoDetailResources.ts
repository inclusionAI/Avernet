import { downloadTodoArtifactContent, loadTodoArtifacts, loadTodoEvents } from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import { useCallback, useEffect, useRef } from 'react';

function sortEvents<T extends { eventSeq: number }>(events: T[]): T[] {
  return [...events].sort((left, right) => left.eventSeq - right.eventSeq);
}

export function useTodoDetailResources(todoId: string | null, hasCurrentDetail: boolean) {
  const storedEvents = useTodoStore((state) => state.events);
  const storedArtifacts = useTodoStore((state) => state.artifacts);
  const eventsLoading = useTodoStore((state) => state.eventsLoading);
  const artifactsLoading = useTodoStore((state) => state.artifactsLoading);
  const eventsError = useTodoStore((state) => state.eventsError);
  const artifactsError = useTodoStore((state) => state.artifactsError);
  const setEvents = useTodoStore((state) => state.setEvents);
  const setArtifacts = useTodoStore((state) => state.setArtifacts);
  const setEventsLoading = useTodoStore((state) => state.setEventsLoading);
  const setArtifactsLoading = useTodoStore((state) => state.setArtifactsLoading);
  const setEventsError = useTodoStore((state) => state.setEventsError);
  const setArtifactsError = useTodoStore((state) => state.setArtifactsError);
  const eventRequestRef = useRef(0);
  const artifactRequestRef = useRef(0);

  const events = hasCurrentDetail ? storedEvents : [];
  const artifacts = hasCurrentDetail
    ? storedArtifacts.filter((item) => item.todoId === todoId)
    : [];

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

  const readArtifactContent = useCallback(
    async (artifactId: string, disposition: 'inline' | 'attachment') => {
      if (!todoId) throw new Error('待办不存在');
      return downloadTodoArtifactContent(todoId, artifactId, disposition);
    },
    [todoId],
  );

  useEffect(
    () => () => {
      eventRequestRef.current += 1;
      artifactRequestRef.current += 1;
    },
    [todoId],
  );

  return {
    events,
    artifacts,
    eventsLoading,
    artifactsLoading,
    eventsError,
    artifactsError,
    refreshEvents,
    refreshArtifacts,
    readArtifactContent,
  };
}
