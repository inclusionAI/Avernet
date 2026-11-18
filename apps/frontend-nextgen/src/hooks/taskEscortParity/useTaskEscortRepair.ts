import type { RepairQuery } from '@/domain/taskEscortParity/repairTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { repairService } from '@/services/taskEscortParity/repairService';
import { useCallback } from 'react';
import { useTaskEscortEvolutionRead } from './useTaskEscortEvolution';
export function useTaskEscortRepairCandidates(context: TaskEscortParityContext, query: RepairQuery, enabled = true) {
  const key = JSON.stringify(query);
  const read = useCallback(
    (signal: AbortSignal) => repairService.candidates(context.workflowId, context.userId!, JSON.parse(key), signal),
    [context.workflowId, context.userId, key],
  );
  return useTaskEscortEvolutionRead(context, `repair-candidates:${key}`, read, enabled);
}
export function useTaskEscortRepairTask(context: TaskEscortParityContext, id: string, page: number) {
  const read = useCallback(
    (signal: AbortSignal) => repairService.task(context.workflowId, id, context.userId!, page, signal),
    [context.workflowId, context.userId, id, page],
  );
  return useTaskEscortEvolutionRead(context, `repair-task:${id}:${page}`, read, !!id);
}
