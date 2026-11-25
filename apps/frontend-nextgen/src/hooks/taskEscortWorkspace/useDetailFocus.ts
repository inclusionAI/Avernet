import { useCallback, useRef } from 'react';

/** Programmatic dialogs have no ModalTrigger for Radix to restore focus to. */
export function useDetailFocusRestore() {
  const opener = useRef<HTMLElement | null>(null);
  const onOpenAutoFocus = useCallback(() => {
    const active = document.activeElement;
    opener.current = active instanceof HTMLElement && active !== document.body ? active : null;
  }, []);
  const onCloseAutoFocus = useCallback((event: Event) => {
    event.preventDefault();
    const target = opener.current;
    opener.current = null;
    if (target?.isConnected && !target.matches(":disabled, [aria-disabled='true']")) {
      target.focus({ preventScroll: true });
      if (document.activeElement === target) return;
    }
    // The opener may disappear after filtering or completing the issue.
    const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"][data-detail-focus-fallback]');
    const fallback =
      dialogs.item(dialogs.length - 1) ?? document.querySelector<HTMLElement>('[data-detail-focus-fallback]');
    fallback?.focus({ preventScroll: true });
  }, []);
  return { onOpenAutoFocus, onCloseAutoFocus };
}
