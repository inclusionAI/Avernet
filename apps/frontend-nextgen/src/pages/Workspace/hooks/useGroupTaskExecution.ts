import { useTaskExecuteFromCard } from '@/hooks/useTaskExecuteFromCard';
import { useTaskExecution } from '@/hooks/useTaskExecution';
import type {
  GroupTaskExecutionOptions,
  UseTaskExecutionResult,
} from '@/extensions/contracts/groupTasks';
import { useGroupTaskComposerContext } from './useGroupTaskComposerContext';

// 入参/返回契约（GroupTaskExecutionOptions / UseTaskExecutionResult）已上移至
// src/extensions/contracts/groupTasks.ts（collab-form Task 17 源切片类型完备），本实现反向注解契约。

/** 统一装配协作群的两个任务 execute 入口，确保 relay 共用定向 holder 出口。 */
export function useGroupTaskExecution(options: GroupTaskExecutionOptions): UseTaskExecutionResult {
  const context = useGroupTaskComposerContext(options.group, options.session, options.activeIdentity);
  const execution = useTaskExecution({
    panelRef: options.panelRef,
    context,
    submitPanelMessage: options.submitPanelMessage,
    submitTaskExecutionMessage: options.submitTaskExecutionMessage,
  });
  useTaskExecuteFromCard({
    panelRef: options.panelRef,
    context,
    submitPanelMessage: options.submitPanelMessage,
    submitTaskExecutionMessage: options.submitTaskExecutionMessage,
    appendAssistantMessage: options.appendAssistantMessage,
    streamAssistantMessage: options.streamAssistantMessage,
  });
  return execution;
}
