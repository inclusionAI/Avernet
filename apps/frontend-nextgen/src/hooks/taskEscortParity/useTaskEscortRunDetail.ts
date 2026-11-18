import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { overviewService } from '@/services/taskEscortParity/overviewService';
import { useCallback } from 'react';
import { useTaskEscortRead } from './useTaskEscortRead';

export function useTaskEscortRunDetail(context: TaskEscortParityContext, runId: string) {
  const { workflowId, userId } = context;
  const read = useCallback(
    (signal: AbortSignal) => overviewService.runDetail(workflowId, runId, { user_id: userId }, signal),
    [workflowId, userId, runId],
  );
  return useTaskEscortRead(context, `overview:run:${runId}`, read);
}
export function useTaskEscortRunWorkflow(context: TaskEscortParityContext) {
  const { workflowId, userId } = context;
  const read = useCallback(
    (signal: AbortSignal) => overviewService.workflow(workflowId, { user_id: userId }, signal),
    [workflowId, userId],
  );
  return useTaskEscortRead(context, 'overview:runSpec', read);
}
export function useTaskEscortRunLogs(context: TaskEscortParityContext, runId: string) {
  const { userId } = context;
  const read = useCallback(
    (signal: AbortSignal) => overviewService.logs(runId, { user_id: userId }, signal),
    [runId, userId],
  );
  return useTaskEscortRead(context, `overview:logs:${runId}`, read);
}
export function useTaskEscortRunArchive(context: TaskEscortParityContext, runId: string) {
  const { workflowId, userId } = context;
  const read = useCallback(
    (signal: AbortSignal) => overviewService.archive(workflowId, runId, { user_id: userId }, signal),
    [workflowId, userId, runId],
  );
  return useTaskEscortRead(context, `overview:archive:${runId}`, read);
}
