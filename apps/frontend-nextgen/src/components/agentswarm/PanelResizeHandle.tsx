import { useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '@/utils/cn';

export interface PanelResizeHandleProps {
  /** Current panel width in pixels. */
  width: number;
  /** Clamp already applied by the caller; this only reports intent. */
  onWidthChange: (width: number) => void;
  min: number;
  max: number;
  /** Accessible name, since the handle is the only affordance for the action. */
  label: string;
}

/**
 * A vertical drag handle for the panel's left edge.
 *
 * Dragging left widens the panel, because the panel is pinned to the right of the
 * conversation. Keyboard users get the same control: arrow keys step the width,
 * Home/End jump to the bounds.
 */
export function PanelResizeHandle({ width, onWidthChange, min, max, label }: PanelResizeHandleProps) {
  const [dragging, setDragging] = useState(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  const clamp = useCallback((next: number) => Math.min(max, Math.max(min, Math.round(next))), [min, max]);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (event: MouseEvent) => {
      // The panel grows when the pointer moves left.
      onWidthChange(clamp(startWidth.current + (startX.current - event.clientX)));
    };
    const onUp = () => setDragging(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    // A drag over text would otherwise select it.
    const previous = document.body.style.userSelect;
    document.body.style.userSelect = 'none';
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.userSelect = previous;
    };
  }, [dragging, clamp, onWidthChange]);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={width}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      onMouseDown={(event) => {
        event.preventDefault();
        startX.current = event.clientX;
        startWidth.current = width;
        setDragging(true);
      }}
      onKeyDown={(event) => {
        const step = event.shiftKey ? 40 : 16;
        if (event.key === 'ArrowLeft') {
          event.preventDefault();
          onWidthChange(clamp(width + step));
        } else if (event.key === 'ArrowRight') {
          event.preventDefault();
          onWidthChange(clamp(width - step));
        } else if (event.key === 'Home') {
          event.preventDefault();
          onWidthChange(min);
        } else if (event.key === 'End') {
          event.preventDefault();
          onWidthChange(max);
        }
      }}
      className={cn(
        'absolute inset-y-0 left-0 z-10 w-1 cursor-col-resize',
        'hover:bg-primary/30 focus-visible:bg-primary/40 focus-visible:outline-none',
        dragging && 'bg-primary/40',
      )}
    />
  );
}