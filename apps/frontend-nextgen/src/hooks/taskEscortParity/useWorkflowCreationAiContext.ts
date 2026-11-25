import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import { useCallback } from 'react';
import { useTaskEscortRead } from './useTaskEscortRead';

/** The unified AI protocol requires an editable, existing workflow as its authorized context. */
export function useWorkflowCreationAiContext(context: TaskEscortParityContext, enabled: boolean, sourceId: string) {
  const ready = enabled && (context.creationReady ?? context.ready) && !!context.userId?.trim() && !!sourceId;
  return useTaskEscortRead(
    { ...context, ready, workflowId: sourceId },
    'workflow-create-ai-context',
    useCallback(
      async (signal: AbortSignal) => {
        if (!context.userId || !sourceId) throw new Error('请选择有编辑权限的来源工作流');
        const identity = { user_id: context.userId };
        const baseline = await workflowAuthoringService.readBaseline(sourceId, identity, signal);
        return baseline.spec;
      },
      [context.userId, sourceId],
    ),
  );
}
