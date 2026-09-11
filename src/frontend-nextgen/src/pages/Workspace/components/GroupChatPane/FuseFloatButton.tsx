import { Button } from '@/components/ui';
import { useFuseStore } from '@/stores/fuseStore';
import { cn } from '@/utils/cn';
import { Brain } from 'lucide-react';
import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

interface FuseFloatButtonProps {
  onClick: () => void;
  sessionId?: string | null;
}

export const FUSE_FLOAT_BUTTON_STORAGE_KEY = 'teamclaw:fuse-float-button-bottom';
const DEFAULT_BOTTOM = 200;
const MIN_BOTTOM = 16;
const BUTTON_HEIGHT = 40; // h-10
const DRAG_MOVE_THRESHOLD = 4;
const KEY_STEP = 12;
const KEY_STEP_LARGE = 40;

function clampBottom(bottom: number, viewportHeight: number): number {
  const maxBottom = Math.max(MIN_BOTTOM, viewportHeight - BUTTON_HEIGHT - MIN_BOTTOM);
  return Math.min(Math.max(Math.round(bottom), MIN_BOTTOM), maxBottom);
}

function readStoredBottom(): number {
  if (typeof window === 'undefined') return DEFAULT_BOTTOM;
  try {
    const raw = window.localStorage.getItem(FUSE_FLOAT_BUTTON_STORAGE_KEY);
    if (raw === null) return DEFAULT_BOTTOM;
    const stored = Number(raw);
    if (!Number.isFinite(stored)) return DEFAULT_BOTTOM;
    return clampBottom(stored, window.innerHeight);
  } catch {
    return DEFAULT_BOTTOM;
  }
}

function persistBottom(bottom: number): void {
  try {
    window.localStorage.setItem(FUSE_FLOAT_BUTTON_STORAGE_KEY, String(Math.round(bottom)));
  } catch {
    // 浏览器禁用存储时仍保留当前会话内拖拽能力。
  }
}

/**
 * 融合模式悬浮按钮：贴右侧边缘，可沿右边缘上下拖动调整位置（位置持久化），
 * hover 展开文字，当前会话有未读回答时显示红点。
 * 拖拽与点击区分：指针位移超过阈值视为拖拽，松手后的 click 不再触发打开。
 */
export function FuseFloatButton({ onClick, sessionId }: FuseFloatButtonProps) {
  const unreadSessionIds = useFuseStore((s) => s.unreadSessionIds);
  const hasUnread = useMemo(() => (sessionId ? !!unreadSessionIds[sessionId] : false), [unreadSessionIds, sessionId]);
  const [hovered, setHovered] = useState(false);
  const [bottom, setBottom] = useState(readStoredBottom);
  const [dragging, setDragging] = useState(false);
  const bottomRef = useRef(bottom);
  const dragStartRef = useRef<{ clientY: number; bottom: number } | null>(null);
  const movedRef = useRef(false);

  const updateBottom = useCallback((next: number) => {
    bottomRef.current = next;
    setBottom(next);
  }, []);

  // 视口尺寸变化时把按钮收敛回可视区域，避免缩放窗口后按钮跑到屏幕外。
  useLayoutEffect(() => {
    const reclamp = () => updateBottom(clampBottom(bottomRef.current, window.innerHeight));
    reclamp();
    window.addEventListener('resize', reclamp);
    return () => window.removeEventListener('resize', reclamp);
  }, [updateBottom]);

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    dragStartRef.current = { clientY: event.clientY, bottom: bottomRef.current };
    movedRef.current = false;
    setDragging(true);
  };

  useEffect(() => {
    if (!dragging) return;
    const onMove = (event: PointerEvent) => {
      const start = dragStartRef.current;
      if (!start) return;
      // 向上拖（clientY 变小）→ bottom 增大
      const deltaY = start.clientY - event.clientY;
      if (Math.abs(deltaY) > DRAG_MOVE_THRESHOLD) movedRef.current = true;
      updateBottom(clampBottom(start.bottom + deltaY, window.innerHeight));
    };
    const onUp = () => {
      dragStartRef.current = null;
      setDragging(false);
      persistBottom(bottomRef.current);
    };
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
    return () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onUp);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
    };
  }, [dragging, updateBottom]);

  const handleClick = () => {
    if (movedRef.current) {
      movedRef.current = false;
      return;
    }
    onClick();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const step = event.shiftKey ? KEY_STEP_LARGE : KEY_STEP;
    let next: number | null = null;
    if (event.key === 'ArrowUp') next = bottomRef.current + step;
    if (event.key === 'ArrowDown') next = bottomRef.current - step;
    if (event.key === 'Home') next = Number.MAX_SAFE_INTEGER; // clamp 收敛到可视区顶部
    if (event.key === 'End') next = MIN_BOTTOM;
    if (next === null) return;
    event.preventDefault();
    updateBottom(clampBottom(next, window.innerHeight));
    persistBottom(bottomRef.current);
  };

  const expanded = hovered || dragging;
  return (
    <div
      data-testid="fuse-float-button"
      className={cn('fixed right-6 z-50 touch-none', !dragging && 'transition-[bottom] duration-150')}
      style={{ bottom }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onPointerDown={handlePointerDown}
    >
      <Button
        variant="ghost"
        size="sm"
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        className={cn(
          'flex h-10 items-center overflow-hidden rounded-full border border-border bg-background text-foreground shadow-md hover:shadow-lg !px-0 !justify-start',
          dragging ? 'cursor-grabbing' : 'cursor-grab',
          expanded ? 'w-[120px]' : 'w-10',
        )}
      >
        <span className="ml-1.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
          <Brain className="h-4 w-4 text-primary" />
        </span>
        <span
          className={cn(
            'whitespace-nowrap pl-2 pr-3 text-sm font-medium transition-opacity',
            expanded ? 'opacity-100' : 'opacity-0',
          )}
        >
          融合模式
        </span>
        {hasUnread && (
          <span className="absolute right-0.5 top-0.5 h-2.5 w-2.5 rounded-full bg-destructive ring-2 ring-background" />
        )}
      </Button>
    </div>
  );
}
