import type { KeyboardEvent, PointerEvent } from 'react';
import { useEffect, useRef, useState } from 'react';

export const SIDEBAR_WIDTH_KEY = 'task-escort.sidebar-width';
const MIN = 220;
const MAX = 360;
const clamp = (width: number) => Math.min(MAX, Math.max(MIN, width));
const desktopQuery = '(min-width: 768px)';

function isDesktop() {
  if (typeof window === 'undefined') return false;
  return typeof window.matchMedia === 'function' ? window.matchMedia(desktopQuery).matches : window.innerWidth >= 768;
}

function initialWidth() {
  try {
    const stored = window.localStorage.getItem(SIDEBAR_WIDTH_KEY);
    const width = stored?.trim() ? Number(stored) : NaN;
    return Number.isFinite(width) ? clamp(width) : 264;
  } catch {
    return 264;
  }
}

export function useSidebarResize(compact: boolean) {
  const [width, setWidth] = useState(initialWidth);
  const currentWidth = useRef(width);
  const drag = useRef<{ id: number; x: number; width: number; target: HTMLDivElement } | null>(null);
  const changeWidth = (value: number) => {
    currentWidth.current = clamp(value);
    setWidth(currentWidth.current);
  };
  const persist = () => {
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_KEY, String(currentWidth.current));
    } catch {
      // A blocked preference store must not disable navigation or resizing.
    }
  };
  const finish = () => {
    const active = drag.current;
    if (!active) return;
    drag.current = null;
    try {
      if (active.target.hasPointerCapture(active.id)) active.target.releasePointerCapture(active.id);
    } catch {
      // Capture may already have been released by the browser.
    }
    persist();
  };
  useEffect(() => {
    const media = typeof window.matchMedia === 'function' ? window.matchMedia(desktopQuery) : undefined;
    const stop = () => finish();
    media?.addEventListener?.('change', stop);
    window.addEventListener('resize', stop);
    window.addEventListener('blur', stop);
    return () => {
      media?.removeEventListener?.('change', stop);
      window.removeEventListener('resize', stop);
      window.removeEventListener('blur', stop);
      finish();
    };
  }, []);
  useEffect(() => {
    if (compact) finish();
  }, [compact]);

  return {
    width,
    handleProps: {
      role: 'separator' as const,
      tabIndex: 0,
      'aria-label': '调整任务护航侧栏宽度',
      'aria-orientation': 'vertical' as const,
      'aria-valuemin': MIN,
      'aria-valuemax': MAX,
      'aria-valuenow': width,
      onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
        if (compact || event.button !== 0 || drag.current || !isDesktop()) return;
        event.preventDefault();
        event.currentTarget.focus();
        try {
          event.currentTarget.setPointerCapture(event.pointerId);
        } catch {
          return;
        }
        drag.current = {
          id: event.pointerId,
          x: event.clientX,
          width: currentWidth.current,
          target: event.currentTarget,
        };
      },
      onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
        const active = drag.current;
        if (active?.id === event.pointerId) {
          event.preventDefault();
          changeWidth(active.width + event.clientX - active.x);
        }
      },
      onPointerUp: (event: PointerEvent<HTMLDivElement>) => {
        if (drag.current?.id === event.pointerId) finish();
      },
      onPointerCancel: (event: PointerEvent<HTMLDivElement>) => {
        if (drag.current?.id === event.pointerId) finish();
      },
      onLostPointerCapture: (event: PointerEvent<HTMLDivElement>) => {
        if (drag.current?.id === event.pointerId) finish();
      },
      onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
        if (compact || !isDesktop()) return;
        const keys: Record<string, number> = { ArrowLeft: width - 10, ArrowRight: width + 10, Home: MIN, End: MAX };
        const next = keys[event.key];
        if (next === undefined) return;
        event.preventDefault();
        changeWidth(next);
        persist();
      },
    },
  };
}
