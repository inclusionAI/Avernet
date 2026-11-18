import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { editorService } from '@/services/taskEscortParity/editorService';
import { useCallback } from 'react';
import { useTaskEscortRead } from './useTaskEscortRead';

export function useTaskEscortEditor(context: TaskEscortParityContext) {
  const { workflowId, userId } = context;
  const readAccess = useCallback(
    (signal: AbortSignal) => editorService.access(workflowId, { user_id: userId }, signal),
    [workflowId, userId],
  );
  const access = useTaskEscortRead({ ...context, ready: context.ready && !!workflowId }, 'editor:access', readAccess);
  const readSpec = useCallback(
    (signal: AbortSignal) => editorService.workflow(workflowId, { user_id: userId }, signal),
    [workflowId, userId],
  );
  const workflow = useTaskEscortRead(
    { ...context, ready: context.ready && !!workflowId && access.data?.canView === true },
    'editor:spec',
    readSpec,
  );
  return { access, workflow };
}
export function useTaskEscortEditorHistory(context: TaskEscortParityContext, enabled: boolean) {
  const { workflowId, userId } = context;
  const read = useCallback(
    (signal: AbortSignal) => editorService.history(workflowId, { user_id: userId }, signal),
    [workflowId, userId],
  );
  return useTaskEscortRead({ ...context, ready: context.ready && enabled }, 'editor:history', read);
}
export function useTaskEscortEditorScripts(context: TaskEscortParityContext, enabled: boolean) {
  const { workflowId, userId } = context;
  const read = useCallback(
    (signal: AbortSignal) => editorService.scripts(workflowId, { user_id: userId }, signal),
    [workflowId, userId],
  );
  return useTaskEscortRead({ ...context, ready: context.ready && enabled }, 'editor:scripts', read);
}
export function useTaskEscortEditorSnapshot(context: TaskEscortParityContext, deploy: number | null) {
  const { workflowId, userId } = context;
  const read = useCallback(
    (signal: AbortSignal) => {
      if (deploy === null) throw new Error('请先选择发布记录');
      return editorService.snapshot(workflowId, deploy, { user_id: userId }, signal);
    },
    [workflowId, userId, deploy],
  );
  return useTaskEscortRead({ ...context, ready: context.ready && deploy !== null }, `editor:snapshot:${deploy}`, read);
}
export function useTaskEscortEditorDiff(context: TaskEscortParityContext, selection: readonly number[] | null) {
  const { workflowId, userId } = context;
  const from = selection?.[0],
    to = selection?.[1];
  const read = useCallback(
    (signal: AbortSignal) => {
      if (from === undefined || to === undefined) throw new Error('请选择两个发布记录');
      return editorService.diff(workflowId, from, to, { user_id: userId }, signal);
    },
    [workflowId, userId, from, to],
  );
  return useTaskEscortRead(
    { ...context, ready: context.ready && from !== undefined && to !== undefined },
    `editor:diff:${from}:${to}`,
    read,
  );
}
