import type { GroupView, IdentityView, SessionView } from '@/domain/collaboration';
import { useTaskExecuteFromCard } from '@/hooks/useTaskExecuteFromCard';
import { useTaskExecution } from '@/hooks/useTaskExecution';
import type { PanelHandle } from '@tc-chat/core';
import type { RefObject } from 'react';
import { useGroupTaskComposerContext } from './useGroupTaskComposerContext';

interface GroupTaskExecutionOptions {
  group: GroupView | null;
  session: SessionView | null;
  activeIdentity?: IdentityView | null;
  panelRef: RefObject<PanelHandle>;
  submitPanelMessage: (content: string) => void;
  submitTaskExecutionMessage: (content: string, holderId: string) => void;
  appendAssistantMessage: (content: string) => void;
  streamAssistantMessage?: (content: string) => Promise<void>;
}

/** 统一装配协作群的两个任务 execute 入口，确保 relay 共用定向 holder 出口。 */
export function useGroupTaskExecution(options: GroupTaskExecutionOptions) {
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
