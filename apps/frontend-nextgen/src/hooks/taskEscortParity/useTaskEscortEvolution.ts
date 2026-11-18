import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { evolutionService } from '@/services/taskEscortParity/evolutionService';
import { useCallback } from 'react';
import { useTaskEscortRead } from './useTaskEscortRead';
/** Every leaf must pass a stable reader and only enable after verified access. */
export function useTaskEscortEvolutionRead<T>(
  context: TaskEscortParityContext,
  key: string,
  read: (signal: AbortSignal) => Promise<T>,
  enabled = true,
) {
  return useTaskEscortRead(
    { ...context, ready: context.ready && !!context.userId && !!context.workflowId && enabled },
    key,
    read,
  );
}
export function useTaskEscortEvolutionAccess(context: TaskEscortParityContext) {
  const read = useCallback(
    (signal: AbortSignal) => evolutionService.access(context.workflowId, context.userId!, signal),
    [context.workflowId, context.userId],
  );
  return useTaskEscortEvolutionRead(context, 'evolution-access', read);
}
