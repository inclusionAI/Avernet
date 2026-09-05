import { cn } from '@/utils/cn';
import { GripVertical } from 'lucide-react';
import type { KeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from 'react';
import { useCallback, useLayoutEffect, useRef, useState } from 'react';

export const WORKSPACE_SIDEBAR_MIN_WIDTH = 280;
export const WORKSPACE_SIDEBAR_DEFAULT_WIDTH = 320;
export const WORKSPACE_SIDEBAR_MAX_WIDTH = 480;
export const WORKSPACE_SIDEBAR_MAX_RATIO = 0.45;
export const WORKSPACE_SIDEBAR_STORAGE_KEY = 'teamclaw:workspace-sidebar-width';

interface ResizableWorkspaceSidebarProps {
  children: ReactNode;
  ariaLabel: string;
  className?: string;
}

function clampWidth(width: number, maxWidth: number): number {
  return Math.min(Math.max(Math.round(width), WORKSPACE_SIDEBAR_MIN_WIDTH), maxWidth);
}

function readStoredWidth(): number {
  if (typeof window === 'undefined') return WORKSPACE_SIDEBAR_DEFAULT_WIDTH;
  try {
    const raw = window.localStorage.getItem(WORKSPACE_SIDEBAR_STORAGE_KEY);
    if (raw === null) return WORKSPACE_SIDEBAR_DEFAULT_WIDTH;
    const stored = Number(raw);
    if (!Number.isFinite(stored)) return WORKSPACE_SIDEBAR_DEFAULT_WIDTH;
    return Math.min(Math.max(Math.round(stored), WORKSPACE_SIDEBAR_MIN_WIDTH), WORKSPACE_SIDEBAR_MAX_WIDTH);
  } catch {
    return WORKSPACE_SIDEBAR_DEFAULT_WIDTH;
  }
}

function persistWidth(width: number): void {
  try {
    window.localStorage.setItem(WORKSPACE_SIDEBAR_STORAGE_KEY, String(Math.round(width)));
  } catch {
    // 浏览器禁用存储时仍保留当前会话内调宽能力。
  }
}

function dynamicMaxWidth(containerWidth: number): number {
  if (containerWidth <= 0) return WORKSPACE_SIDEBAR_MAX_WIDTH;
  return Math.max(
    WORKSPACE_SIDEBAR_MIN_WIDTH,
    Math.min(WORKSPACE_SIDEBAR_MAX_WIDTH, Math.floor(containerWidth * WORKSPACE_SIDEBAR_MAX_RATIO)),
  );
}

export function ResizableWorkspaceSidebar({
  children,
  ariaLabel,
  className,
}: ResizableWorkspaceSidebarProps) {
  const sidebarRef = useRef<HTMLElement | null>(null);
  const [preferredWidth, setPreferredWidth] = useState(readStoredWidth);
  const preferredWidthRef = useRef(preferredWidth);
  const dragStartRef = useRef<{ clientX: number; width: number } | null>(null);
  const [maxWidth, setMaxWidth] = useState(WORKSPACE_SIDEBAR_MAX_WIDTH);
  const [dragging, setDragging] = useState(false);
  const width = clampWidth(preferredWidth, maxWidth);

  const updatePreferredWidth = useCallback((nextWidth: number) => {
    preferredWidthRef.current = nextWidth;
    setPreferredWidth(nextWidth);
  }, []);

  useLayoutEffect(() => {
    const parent = sidebarRef.current?.parentElement;
    if (!parent) return;
    const updateMaxWidth = () => setMaxWidth(dynamicMaxWidth(parent.getBoundingClientRect().width));
    updateMaxWidth();
    window.addEventListener('resize', updateMaxWidth);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(updateMaxWidth);
    observer?.observe(parent);
    return () => {
      window.removeEventListener('resize', updateMaxWidth);
      observer?.disconnect();
    };
  }, []);

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    dragStartRef.current = { clientX: event.clientX, width };
    setDragging(true);
  };

  const setAndPersistWidth = (nextWidth: number) => {
    const next = clampWidth(nextWidth, maxWidth);
    updatePreferredWidth(next);
    persistWidth(next);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 24 : 8;
    let nextWidth: number | null = null;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') nextWidth = width - step;
    if (event.key === 'ArrowRight' || event.key === 'ArrowUp') nextWidth = width + step;
    if (event.key === 'Home') nextWidth = WORKSPACE_SIDEBAR_MIN_WIDTH;
    if (event.key === 'End') nextWidth = maxWidth;
    if (nextWidth === null) return;
    event.preventDefault();
    setAndPersistWidth(nextWidth);
  };

  const resetWidth = () => {
    updatePreferredWidth(WORKSPACE_SIDEBAR_DEFAULT_WIDTH);
    persistWidth(WORKSPACE_SIDEBAR_DEFAULT_WIDTH);
  };

  const onMove = (event: PointerEvent) => {
    const start = dragStartRef.current;
    if (!start) return;
    updatePreferredWidth(clampWidth(start.width + event.clientX - start.clientX, maxWidth));
  };
  const onUp = () => {
    dragStartRef.current = null;
    setDragging(false);
    persistWidth(preferredWidthRef.current);
  };

  useLayoutEffect(() => {
    if (!dragging) return;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    return () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
    };
  }, [dragging, maxWidth, updatePreferredWidth]);

  return (
    <aside
      ref={sidebarRef}
      aria-label={ariaLabel}
      style={{ width: `${width}px` }}
      className={cn(
        'relative hidden shrink-0 flex-col overflow-visible border-r border-border bg-muted/20 lg:flex',
        className,
      )}
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>

      <div
        role="separator"
        aria-label="调整对话协作左栏宽度"
        aria-orientation="vertical"
        aria-valuemin={WORKSPACE_SIDEBAR_MIN_WIDTH}
        aria-valuemax={maxWidth}
        aria-valuenow={width}
        tabIndex={0}
        data-testid="workspace-sidebar-resizer"
        onPointerDown={handlePointerDown}
        onDoubleClick={resetWidth}
        onKeyDown={handleKeyDown}
        className="group/resizer absolute bottom-0 right-0 top-0 z-30 w-2.5 translate-x-1/2 touch-none cursor-col-resize outline-none"
      >
        {/* 常驻拖拽提示胶囊：垂直居中贴边缝，hover/focus/拖拽时转品牌色。 */}
        <span
          aria-hidden="true"
          data-testid="workspace-sidebar-grip"
          className={cn(
            'absolute left-1/2 top-1/2 flex h-7 w-3.5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-sm transition-colors',
            dragging
              ? 'border-primary text-primary'
              : 'border-border group-hover/resizer:border-primary group-hover/resizer:text-primary group-focus-visible/resizer:border-primary group-focus-visible/resizer:text-primary',
          )}
        >
          <GripVertical className="h-3.5 w-3.5" />
        </span>
      </div>
    </aside>
  );
}
