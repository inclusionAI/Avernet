import { isEditorPeriodOnlyChange } from '@/services/taskEscortWorkspace/editorNavigation';
import { history } from '@umijs/max';
import { useCallback, useEffect, useRef, useState } from 'react';

export interface EditorLeaveRequest {
  proceed: () => void;
}
/** History v5 blocks PUSH/REPLACE and router-managed browser POP without patching browser methods. */
export function useTaskEscortWorkspaceEditorNavigation(dirty: boolean, restore: () => void, workflowId: string) {
  const [leave, setLeave] = useState<EditorLeaveRequest | null>(null);
  const unblock = useRef<(() => void) | null>(null);
  const pending = useRef<EditorLeaveRequest | null>(null);
  const stopSafeTransition = useRef<(() => void) | null>(null);
  const restoreRef = useRef(restore);
  restoreRef.current = restore;
  const requestLeave = useCallback(
    (proceed: () => void) => {
      if (!dirty) {
        proceed();
        return;
      }
      const request = { proceed };
      pending.current = request;
      setLeave(request);
    },
    [dirty],
  );
  useEffect(() => {
    if (!dirty) {
      pending.current = null;
      setLeave(null);
      return;
    }
    let active = true;
    const block: Parameters<typeof history.block>[0] = (transition) => {
      if (isEditorPeriodOnlyChange(history.location, transition.location)) {
        // POP retry is asynchronous. Rearm only after the retry commits, or it would block itself again.
        unblock.current?.();
        unblock.current = null;
        stopSafeTransition.current = history.listen(() => {
          stopSafeTransition.current?.();
          stopSafeTransition.current = null;
          if (active) unblock.current = history.block(block);
        });
        transition.retry();
        return;
      }
      const request = { proceed: () => transition.retry() };
      pending.current = request;
      setLeave(request);
    };
    unblock.current = history.block(block);
    const unload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', unload);
    return () => {
      active = false;
      stopSafeTransition.current?.();
      stopSafeTransition.current = null;
      unblock.current?.();
      unblock.current = null;
      window.removeEventListener('beforeunload', unload);
    };
  }, [dirty, workflowId]);
  const cancelLeave = useCallback(() => {
    pending.current = null;
    setLeave(null);
  }, []);
  const discardAndLeave = useCallback(() => {
    const request = pending.current;
    if (!request) return;
    pending.current = null;
    setLeave(null);
    // Release before retry (history v5's documented blocker contract), including workflow query changes.
    unblock.current?.();
    unblock.current = null;
    restoreRef.current();
    request.proceed();
  }, []);
  return { leave, requestLeave, cancelLeave, discardAndLeave };
}
