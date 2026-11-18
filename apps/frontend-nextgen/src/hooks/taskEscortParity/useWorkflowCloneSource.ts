import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import { useCallback } from 'react';
import { useTaskEscortRead } from './useTaskEscortRead';

/** Clone candidates are saved-spec sources, not workflows inferred from run history. */
export function useWorkflowCloneSource(context: TaskEscortParityContext, enabled: boolean, sourceId: string) {
  const ready = enabled && (context.creationReady ?? context.ready) && !!context.userId?.trim();
  const listContext = { ...context, ready, workflowId: '' };
  const list = useTaskEscortRead(
    listContext,
    'workflow-clone-candidates',
    useCallback(
      (signal: AbortSignal) => {
        if (!context.userId) throw new Error('请先加载当前用户身份');
        return workflowAuthoringService.listCloneCandidates({ user_id: context.userId }, signal);
      },
      [context.userId],
    ),
  );
  const preview = useTaskEscortRead(
    { ...listContext, ready: ready && !!sourceId, workflowId: sourceId },
    'workflow-clone-preview',
    useCallback(
      (signal: AbortSignal) => {
        if (!context.userId) throw new Error('请先加载当前用户身份');
        return workflowAuthoringService.readClonePreview(sourceId, { user_id: context.userId }, signal);
      },
      [context.userId, sourceId],
    ),
  );
  return { list, preview };
}
