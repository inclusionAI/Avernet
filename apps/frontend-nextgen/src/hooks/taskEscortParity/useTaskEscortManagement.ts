import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { managementNotificationKeys } from '@/services/taskEscortParity/managementError';
import { managementService } from '@/services/taskEscortParity/managementService';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useCallback } from 'react';
import { useTaskEscortRead } from './useTaskEscortRead';

/** Require an explicit actor, never the global httpClient identity fallback. */
export function useTaskEscortManagementResource<T>(
  context: TaskEscortParityContext,
  key: string,
  read: (signal: AbortSignal) => Promise<T>,
  enabled = true,
) {
  const safeRead = useCallback(
    async (signal: AbortSignal): Promise<T> => {
      try {
        return await read(signal);
      } catch (error) {
        const keys = managementNotificationKeys(error);
        // Always cancel in the rejection microtask, even after abort/unmount/scope invalidation.
        // The global observer defers delivery to setTimeout(0), so this precedes its flush.
        if (keys.toastKey) useErrorNotifyStore.getState().cancel(keys.toastKey);
        if (keys.notificationKey && keys.notificationKey !== keys.toastKey)
          useErrorNotifyStore.getState().cancel(keys.notificationKey);
        throw error;
      }
    },
    [read],
  );
  return useTaskEscortRead(
    { ...context, ready: context.ready && !!context.userId?.trim() && !!context.workflowId && enabled },
    `management:${key}`,
    safeRead,
  );
}
export function useTaskEscortManagement(context: TaskEscortParityContext) {
  const accessRead = useCallback(
    (signal: AbortSignal) => managementService.access(context.workflowId, context.userId!, signal),
    [context.workflowId, context.userId],
  );
  const access = useTaskEscortManagementResource(context, 'access', accessRead);
  const historyRead = useCallback(
    (signal: AbortSignal) => managementService.history(context.workflowId, context.userId!, signal),
    [context.workflowId, context.userId],
  );
  const history = useTaskEscortManagementResource(context, 'history', historyRead, access.data?.canView === true);
  return { access, history, canEdit: access.data?.canView === true && access.data.canEdit === true };
}
