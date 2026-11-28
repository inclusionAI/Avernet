import { notifySuccess } from '@/components/ui/notify';
import type { CreateTodoProductInteractionInput, TodoRecord } from '@/domain/todos/models';
import { canInteractWithTodo } from '@/domain/todos/status';
import {
  addTodoProductInteraction,
  loadTodoProductInteractionPage,
  loadTodoProgress,
  stopTodoProductInteraction,
} from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import { safeReportError } from '@/utils/errorToast';
import { useCallback, useEffect, useRef } from 'react';

interface TodoProductRuntimeOptions {
  todoId: string | null;
  selectedTodo: TodoRecord | null;
  hasCurrentDetail: boolean;
  onListRefresh?: () => Promise<void> | void;
}

export function useTodoProductRuntime({
  todoId,
  selectedTodo,
  hasCurrentDetail,
  onListRefresh,
}: TodoProductRuntimeOptions) {
  const storedProgress = useTodoStore((state) => state.progress);
  const storedPage = useTodoStore((state) => state.productInteractionPage);
  const progressLoading = useTodoStore((state) => state.progressLoading);
  const productInteractionLoading = useTodoStore((state) => state.productInteractionLoading);
  const progressError = useTodoStore((state) => state.progressError);
  const productInteractionError = useTodoStore((state) => state.productInteractionError);
  const setProgress = useTodoStore((state) => state.setProgress);
  const setPage = useTodoStore((state) => state.setProductInteractionPage);
  const setProgressLoading = useTodoStore((state) => state.setProgressLoading);
  const setProductInteractionLoading = useTodoStore((state) => state.setProductInteractionLoading);
  const setProgressError = useTodoStore((state) => state.setProgressError);
  const setProductInteractionError = useTodoStore((state) => state.setProductInteractionError);
  const setMutationLoading = useTodoStore((state) => state.setMutationLoading);
  const progressRequestRef = useRef(0);
  const interactionRequestRef = useRef(0);

  const progress = hasCurrentDetail && storedProgress?.todoId === todoId ? storedProgress : null;
  const productInteractionPage = hasCurrentDetail ? storedPage : null;
  const isProductInteractionRuntime = progress?.runtimeKind === 'product_collet';

  const refreshProductInteractions = useCallback(async () => {
    if (!todoId) return;
    const requestId = ++interactionRequestRef.current;
    setProductInteractionLoading(true);
    setProductInteractionError(null);
    try {
      const page = await loadTodoProductInteractionPage(todoId);
      if (requestId === interactionRequestRef.current) setPage(page);
    } catch (cause) {
      if (requestId !== interactionRequestRef.current) return;
      setPage(null);
      setProductInteractionError(cause instanceof Error ? cause.message : '互动加载失败');
    } finally {
      if (requestId === interactionRequestRef.current) setProductInteractionLoading(false);
    }
  }, [setPage, setProductInteractionError, setProductInteractionLoading, todoId]);

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
        if (
          todoProgress.runtimeKind === 'product_collet' ||
          todoProgress.runtimeKind === 'product_runtime_pending'
        ) {
          await refreshProductInteractions();
        } else {
          setPage(null);
          setProductInteractionError(null);
        }
      } catch (cause) {
        if (requestId !== progressRequestRef.current) return;
        setProgress(null);
        setProgressError(cause instanceof Error ? cause.message : '执行进度加载失败');
      } finally {
        if (requestId === progressRequestRef.current) setProgressLoading(false);
      }
    },
    [
      refreshProductInteractions,
      setPage,
      setProductInteractionError,
      setProgress,
      setProgressError,
      setProgressLoading,
      todoId,
    ],
  );

  const productInteract = useCallback(
    async (input: CreateTodoProductInteractionInput) => {
      if (
        !todoId ||
        !selectedTodo ||
        !isProductInteractionRuntime ||
        !canInteractWithTodo(selectedTodo.status)
      ) {
        safeReportError(new Error('当前待办不支持 Product 互动'), { title: '互动失败' });
        return false;
      }
      const text = input.message.trim();
      if (!text) {
        safeReportError(new Error('请输入互动内容'), { title: '互动失败' });
        return false;
      }
      setMutationLoading(true);
      try {
        await addTodoProductInteraction(todoId, {
          message: text,
          replyToInteractionId: input.replyToInteractionId,
        });
        notifySuccess('互动已提交');
        await refreshProductInteractions();
        void refreshProgress();
        await onListRefresh?.();
        return true;
      } catch (cause) {
        safeReportError(cause, { title: '提交互动失败' });
        await refreshProductInteractions();
        return false;
      } finally {
        setMutationLoading(false);
      }
    },
    [
      isProductInteractionRuntime,
      onListRefresh,
      refreshProductInteractions,
      refreshProgress,
      selectedTodo,
      setMutationLoading,
      todoId,
    ],
  );

  const productStopInteraction = useCallback(
    async (interactionId: string) => {
      if (!todoId || !isProductInteractionRuntime) {
        safeReportError(new Error('当前待办不支持停止'), { title: '停止失败' });
        return false;
      }
      setMutationLoading(true);
      try {
        await stopTodoProductInteraction(todoId, interactionId);
        notifySuccess('停止请求已提交');
        await refreshProductInteractions();
        await onListRefresh?.();
        return true;
      } catch (cause) {
        safeReportError(cause, { title: '停止失败' });
        await refreshProductInteractions();
        return false;
      } finally {
        setMutationLoading(false);
      }
    },
    [
      isProductInteractionRuntime,
      onListRefresh,
      refreshProductInteractions,
      setMutationLoading,
      todoId,
    ],
  );

  useEffect(
    () => () => {
      progressRequestRef.current += 1;
      interactionRequestRef.current += 1;
    },
    [todoId],
  );

  return {
    progress,
    progressLoading,
    progressError,
    productInteractionPage,
    productInteractionLoading,
    productInteractionError,
    isProductInteractionRuntime,
    refreshProgress,
    refreshProductInteractions,
    productInteract,
    productStopInteraction,
  };
}
