import type { TodoSpace } from '@/domain/todos/models';
import { loadTodoSpaces } from '@/services/todos/todoService';
import { safeReportError } from '@/utils/errorToast';
import { useCallback, useEffect, useRef, useState } from 'react';

const SPACE_CONTEXT_STORAGE_KEY = 'tc_space_context_current_id';

function readPreferredSpaceId(): number | undefined {
  try {
    const raw = localStorage.getItem(SPACE_CONTEXT_STORAGE_KEY);
    const value = raw ? Number(raw) : Number.NaN;
    return Number.isInteger(value) && value > 0 ? value : undefined;
  } catch {
    return undefined;
  }
}

function pickTodoSpace(spaces: TodoSpace[], preferredId: number | undefined): TodoSpace | undefined {
  return (
    spaces.find((space) => space.spaceId === preferredId) ??
    spaces.find((space) => space.spaceType === 'PERSONAL') ??
    spaces[0]
  );
}

/** Todo 专用空间上下文：只读 Task Engine `/api/v1`，不触发 Admin Space OpenAPI。 */
export function useTodoSpaces() {
  const preferredId = useRef(readPreferredSpaceId());
  const requestId = useRef(0);
  const [spaces, setSpaces] = useState<TodoSpace[]>([]);
  const [currentSpace, setCurrentSpace] = useState<TodoSpace>();
  const [initialized, setInitialized] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  const refresh = useCallback(async () => {
    const currentRequestId = ++requestId.current;
    setLoading(true);
    setError(undefined);
    try {
      const nextSpaces = await loadTodoSpaces();
      if (currentRequestId !== requestId.current) return;
      const selected = pickTodoSpace(nextSpaces, preferredId.current);
      preferredId.current = selected?.spaceId;
      setSpaces(nextSpaces);
      setCurrentSpace(selected);
      setInitialized(true);
    } catch (cause) {
      if (currentRequestId !== requestId.current) return;
      setSpaces([]);
      setCurrentSpace(undefined);
      setInitialized(false);
      setError(cause instanceof Error ? cause.message : '加载空间列表失败');
      safeReportError(cause, { title: '加载空间列表失败' });
    } finally {
      if (currentRequestId === requestId.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      requestId.current += 1;
    };
  }, [refresh]);

  return {
    spaces,
    currentSpace,
    currentSpaceId: currentSpace?.spaceId,
    initialized,
    loading,
    error,
    refresh,
  };
}
