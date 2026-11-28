import { notifySuccess } from '@/components/ui/notify';
import type { CreateTodoInteractionInput, TodoAcceptanceInput, TodoRecord } from '@/domain/todos/models';
import { canInteractWithTodo } from '@/domain/todos/status';
import { acceptTodo, addTodoInteraction } from '@/services/todos/todoService';
import { useTodoStore } from '@/stores/todoStore';
import { safeReportError } from '@/utils/errorToast';
import { useCallback } from 'react';

interface TodoDetailActionsOptions {
  todoId: string | null;
  selectedTodo: TodoRecord | null;
  refresh: () => Promise<void>;
  onListRefresh?: () => Promise<void> | void;
}

export function useTodoDetailActions({
  todoId,
  selectedTodo,
  refresh,
  onListRefresh,
}: TodoDetailActionsOptions) {
  const setMutationLoading = useTodoStore((state) => state.setMutationLoading);

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

  return { interact, accept };
}
