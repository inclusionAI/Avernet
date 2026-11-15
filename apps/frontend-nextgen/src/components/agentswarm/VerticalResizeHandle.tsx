import { useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '@/utils/cn';

export interface VerticalResizeHandleProps {
  height: number;
  onHeightChange: (height: number) => void;
  min: number;
  max: number;
  label: string;
}

/**
 * A horizontal grab bar under an editor, for setting its height.
 *
 * Dragging down grows the editor. Keyboard users get the same control: the arrow
 * keys step the height and Home/End jump to the bounds.
 */
export function VerticalResizeHandle({ height, onHeightChange, min, max, label }: VerticalResizeHandleProps) {
  const [dragging, setDragging] = useState(false);
  const startY = useRef(0);
  const startHeight = useRef(0);

  const clamp = useCallback((next: number) => Math.min(max, Math.max(min, Math.round(next))), [min, max]);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (event: MouseEvent) => onHeightChange(clamp(startHeight.current + (event.clientY - startY.current)));
    const onUp = () => setDragging(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    const previous = document.body.style.userSelect;
    document.body.style.userSelect = 'none';
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.userSelect = previous;
    };
  }, [dragging, clamp, onHeightChange]);

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label={label}
      aria-valuenow={height}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      onMouseDown={(event) => {
        event.preventDefault();
        startY.current = event.clientY;
        startHeight.current = height;
        setDragging(true);
      }}
      onKeyDown={(event) => {
        const step = event.shiftKey ? 60 : 24;
        if (event.key === 'ArrowDown') {
          event.preventDefault();
          onHeightChange(clamp(height + step));
        } else if (event.key === 'ArrowUp') {
          event.preventDefault();
          onHeightChange(clamp(height - step));
        } else if (event.key === 'Home') {
          event.preventDefault();
          onHeightChange(min);
        } else if (event.key === 'End') {
          event.preventDefault();
          onHeightChange(max);
        }
      }}
      className={cn(
        'group flex h-2 cursor-row-resize items-center justify-center rounded',
        'hover:bg-primary/20 focus-visible:bg-primary/30 focus-visible:outline-none',
        dragging && 'bg-primary/30',
      )}
    >
      <span className="h-0.5 w-8 rounded-full bg-border group-hover:bg-primary/50" aria-hidden />
    </div>
  );
}