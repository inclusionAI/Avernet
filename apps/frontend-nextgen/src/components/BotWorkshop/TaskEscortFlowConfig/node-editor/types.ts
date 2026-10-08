import type { TaskEscortWorkflowNode, TaskEscortWorkflowSpec } from '@/components/BotWorkshop/TaskEscort/types';

export type NodeDetailTabId = 'basic' | 'advanced' | 'actions' | 'alerts';

export interface NodeEditorTabProps {
  node: TaskEscortWorkflowNode;
  spec: TaskEscortWorkflowSpec;
  onChange: (updates: Partial<TaskEscortWorkflowNode>) => void;
}
