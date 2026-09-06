import type { TaskEscortWorkflowNode } from '@/components/BotWorkshop/TaskEscort/types';

export function hasAdvancedConfig(node: TaskEscortWorkflowNode): boolean {
  return (
    !!node.retry ||
    !!node.timeoutMs ||
    !!node.config ||
    Object.keys(node.input ?? {}).length > 0 ||
    Object.keys(node.output ?? {}).length > 0
  );
}

export function hasPostActions(node: TaskEscortWorkflowNode): boolean {
  return Array.isArray(node.onResult) && node.onResult.length > 0;
}
