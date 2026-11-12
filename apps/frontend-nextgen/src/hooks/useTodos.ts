import { notifySuccess } from '@/components/ui/notify';
import type { TodoStatus } from '@/domain/todos/models';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { addTodo, loadTodos } from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import { safeReportError } from '@/utils/errorToast';
import { useCallback, useEffect, useRef, useState } from 'react';

const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 10;

export function useTodos() {
  const currentSpaceId = useSpaceContext((state) => state.currentSpaceId);
  const currentSpace = useSpaceContext((state) => state.currentSpace);
  const initialized = useSpaceContext((state) => state.initialized);
  const [page, setPage] = useState(DEFAULT_PAGE);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [keyword, setKeywordState] = useState('');
  const [status, setStatusState] = useState<TodoStatus | undefined>();
  const [botId, setBotIdState] = useState<string | undefined>();
  const requestId = useRef(0);

  const todos = useTodoStore((state) => state.todos);
  const total = useTodoStore((state) => state.total);
  const loading = useTodoStore((state) => state.listLoading);
  const error = useTodoStore((state) => state.listError);
  const mutationLoading = useTodoStore((state) => state.mutationLoading);
  const setTodos = useTodoStore((state) => state.setTodos);
  const setLoading = useTodoStore((state) => state.setListLoading);
  const setError = useTodoStore((state) => state.setListError);
  const setMutationLoading = useTodoStore((state) => state.setMutationLoading);

  const refresh = useCallback(async () => {
    if (currentSpaceId === undefined) {
      setTodos([], 0);
      return;
    }
    const currentRequestId = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const result = await loadTodos({
        spaceId: currentSpaceId,
        keyword: keyword.trim() || undefined,
        status,
        botId,
        page,
        pageSize,
      });
      if (currentRequestId === requestId.current) setTodos(result.items, result.total);
    } catch (cause) {
      if (currentRequestId === requestId.current) {
        setError(cause instanceof Error ? cause.message : '待办加载失败');
        safeReportError(cause, { title: '待办加载失败' });
      }
    } finally {
      if (currentRequestId === requestId.current) setLoading(false);
    }
  }, [botId, currentSpaceId, keyword, page, pageSize, setError, setLoading, setTodos, status]);

  useEffect(() => {
    if (!initialized || currentSpaceId === undefined) return;
    void refresh();
  }, [currentSpaceId, initialized, refresh]);

  const setKeyword = useCallback((value: string) => {
    setKeywordState(value);
    setPage(DEFAULT_PAGE);
  }, []);

  const setStatus = useCallback((value: TodoStatus | undefined) => {
    setStatusState(value);
    setPage(DEFAULT_PAGE);
  }, []);

  const setBotId = useCallback((value: string | undefined) => {
    setBotIdState(value);
    setPage(DEFAULT_PAGE);
  }, []);

  const create = useCallback(
    async (input: { title: string; description: string; botId: string }) => {
      if (currentSpaceId === undefined) {
        safeReportError(new Error('当前空间未就绪'), { title: '创建待办失败' });
        return null;
      }
      setMutationLoading(true);
      try {
        const todo = await addTodo({ ...input, spaceId: currentSpaceId });
        notifySuccess('待办已创建，将等待系统自动调度');
        await refresh();
        return todo;
      } catch (cause) {
        safeReportError(cause, { title: '创建待办失败' });
        return null;
      } finally {
        setMutationLoading(false);
      }
    },
    [currentSpaceId, refresh, setMutationLoading],
  );

  return {
    currentSpace,
    currentSpaceId,
    todos,
    total,
    page,
    pageSize,
    keyword,
    status,
    botId,
    loading,
    mutationLoading,
    error,
    setPage,
    setPageSize: (value: number) => {
      setPageSize(value);
      setPage(DEFAULT_PAGE);
    },
    setKeyword,
    setStatus,
    setBotId,
    refresh,
    create,
  };
}
