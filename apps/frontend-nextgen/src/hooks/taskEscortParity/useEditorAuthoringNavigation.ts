import { history } from '@umijs/max';
import { useCallback, useEffect, useRef, useState } from 'react';

/** Focus/period changes stay inside this editor; identity/workflow/page changes do not. */
function sameEditor(current: typeof history.location, next: typeof history.location) {
  if (current.pathname !== next.pathname || current.hash !== next.hash) return false;
  const from = new URLSearchParams(current.search),
    to = new URLSearchParams(next.search);
  if ((from.get('view') ?? from.get('tab')) !== 'editor' || (to.get('view') ?? to.get('tab')) !== 'editor')
    return false;
  for (const key of ['nodeId', 'period']) {
    from.delete(key);
    to.delete(key);
  }
  from.sort();
  to.sort();
  return from.toString() === to.toString();
}

export function useEditorAuthoringNavigation(dirty: boolean) {
  const [leave, setLeave] = useState<(() => void) | null>(null);
  const unblock = useRef<(() => void) | null>(null);
  const pending = useRef<(() => void) | null>(null);
  const rearm = useRef<(() => void) | null>(null);
  const requestLeave = useCallback(
    (proceed: () => void) => {
      if (!dirty) {
        proceed();
        return;
      }
      pending.current = proceed;
      setLeave(() => proceed);
    },
    [dirty],
  );
  useEffect(() => {
    if (!dirty) {
      pending.current = null;
      setLeave(null);
      return;
    }
    let alive = true;
    const block: Parameters<typeof history.block>[0] = (transition) => {
      if (sameEditor(history.location, transition.location)) {
        unblock.current?.();
        unblock.current = null;
        rearm.current = history.listen(() => {
          rearm.current?.();
          rearm.current = null;
          if (alive) unblock.current = history.block(block);
        });
        transition.retry();
      } else requestLeave(() => transition.retry());
    };
    unblock.current = history.block(block);
    const unload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', unload);
    return () => {
      alive = false;
      rearm.current?.();
      unblock.current?.();
      unblock.current = null;
      window.removeEventListener('beforeunload', unload);
    };
  }, [dirty, requestLeave]);
  const cancelLeave = useCallback(() => {
    pending.current = null;
    setLeave(null);
  }, []);
  const discardAndLeave = useCallback(() => {
    const proceed = pending.current;
    pending.current = null;
    setLeave(null);
    unblock.current?.();
    unblock.current = null;
    proceed?.();
  }, []);
  return { leave, requestLeave, cancelLeave, discardAndLeave };
}
