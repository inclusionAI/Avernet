import type { ButtonHTMLAttributes } from 'react';
import { useEffect, useRef, useState } from 'react';

type TriggerHandlers = Pick<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'onBlur' | 'onFocus' | 'onPointerDown' | 'onPointerLeave' | 'onPointerMove'
>;

/** 折叠导航身份入口的 Tooltip 状态：身份切换后保持关闭，直到指针/焦点真正离开再进入。 */
export function useCollapsedIdentityTooltip(activeId: string | null, popoverOpen: boolean) {
  const [open, setOpen] = useState(false);
  const [suppressed, setSuppressed] = useState(false);
  const previousActiveIdRef = useRef(activeId);

  const closeAndSuppress = () => {
    setOpen(false);
    setSuppressed(true);
  };

  useEffect(() => {
    const previousActiveId = previousActiveIdRef.current;
    previousActiveIdRef.current = activeId;
    if (previousActiveId && previousActiveId !== activeId) closeAndSuppress();
  }, [activeId]);

  const triggerHandlers: TriggerHandlers = {
    onPointerMove: () => {
      if (!suppressed && !popoverOpen) setOpen(true);
    },
    onPointerDown: closeAndSuppress,
    onPointerLeave: () => {
      setOpen(false);
      setSuppressed(false);
    },
    onFocus: () => {
      if (!suppressed && !popoverOpen) setOpen(true);
    },
    onBlur: () => {
      setOpen(false);
      setSuppressed(false);
    },
  };

  return {
    open: open && !suppressed && !popoverOpen,
    onOpenChange: (nextOpen: boolean) => {
      if (nextOpen && (suppressed || popoverOpen)) return;
      setOpen(nextOpen);
    },
    closeAndSuppress,
    triggerHandlers,
  };
}
