/**
 * useComposerSend —— 发送拦截：有任务选中态时构造 /task 指令消息发到会话（由 bot/skill 解析触发），
 * 否则正常发送（透传 context 与 fileRefs）。发送后清空 draft + 清除选中态。
 *
 * 指令格式：
 * - 动态任务：/task {指令}
 * - 工作流任务：/task workflow_id='{facade.command}' {指令}
 */
import { useCallback } from 'react';
import type { SubmitContext } from '@tc-chat/ui/es/Sender';
import type { UseTaskExecutionResult } from './useTaskExecution';
import { buildTaskInstruction } from '@/services/tasks/taskMapper';

export interface ComposerSendDeps {
  /** 正常发送（无任务选中态）：透传 content + context（含 fileRefs）。 */
  sendMessage: (content: string, context?: SubmitContext) => void;
  clearDraft: () => void;
}

export function useComposerSend(taskExecution: UseTaskExecutionResult, deps: ComposerSendDeps) {
  const { selectedWorkflow, pendingDynamic, clearSelection } = taskExecution;
  const { sendMessage, clearDraft } = deps;
  return useCallback(
    (content: string, context?: SubmitContext) => {
      if (!content.trim()) return;
      if (selectedWorkflow || pendingDynamic) {
        // 任务指令不带文件附件，直接发送格式化指令。
        sendMessage(buildTaskInstruction(content, selectedWorkflow, pendingDynamic), undefined);
        clearSelection();
        clearDraft();
        return;
      }
      sendMessage(content, context);
    },
    [selectedWorkflow, pendingDynamic, clearSelection, sendMessage, clearDraft],
  );
}
