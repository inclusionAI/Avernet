import { Button, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import { cn } from '@/utils/cn';
import { ChevronLeft, ChevronRight, GripVertical } from 'lucide-react';
import type { KeyboardEvent, ReactNode, PointerEvent as ReactPointerEvent } from 'react';
import { useCallback, useId, useLayoutEffect, useRef, useState } from 'react';

export const WORKSPACE_SIDEBAR_MIN_WIDTH = 280;
export const WORKSPACE_SIDEBAR_DEFAULT_WIDTH = 320;
export const WORKSPACE_SIDEBAR_MAX_WIDTH = 480;
export const WORKSPACE_SIDEBAR_MAX_RATIO = 0.45;
export const WORKSPACE_SIDEBAR_COLLAPSED_WIDTH = 56;
export const WORKSPACE_SIDEBAR_STORAGE_KEY = 'teamclaw:workspace-sidebar-width';
export const WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY = 'teamclaw:workspace-sidebar-collapsed';

interface ResizableWorkspaceSidebarProps extends Omit<React.HTMLAttributes<HTMLElement>, 'children' | 'className'> {
  children: ReactNode;
  ariaLabel: string;
  className?: string;
  collapsedContent?: ReactNode;
  /** 挂载边缘：left=左侧栏（默认，手柄右缘、拖右变宽、含折叠态）；right=右侧副屏（手柄左缘、拖左变宽、方向语义反转、无折叠态、小屏不隐藏）。 */
  side?: 'left' | 'right';
  /** 独立宽度组（默认为对话协作左栏常量）；右侧副屏按需传入。 */
  minWidth?: number;
  maxWidth?: number;
  defaultWidth?: number;
  /** 宽度持久化 key（默认为左栏 key）；不同面板传独立 key 避免互相覆盖。 */
  storageKey?: string;
}

function clampWidth(width: number, minWidth: number, maxWidth: number): number {
  return Math.min(Math.max(Math.round(width), minWidth), maxWidth);
}

function readStoredWidth(key: string, defaultWidth: number, minWidth: number, maxWidth: number): number {
  if (typeof window === 'undefined') return defaultWidth;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return defaultWidth;
    const stored = Number(raw);
    if (!Number.isFinite(stored)) return defaultWidth;
    return Math.min(Math.max(Math.round(stored), minWidth), maxWidth);
  } catch {
    return defaultWidth;
  }
}

function persistWidth(key: string, width: number): void {
  try {
    window.localStorage.setItem(key, String(Math.round(width)));
  } catch {
    // 浏览器禁用存储时仍保留当前会话内调宽能力。
  }
}

function readStoredCollapsed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function persistCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed));
  } catch {
    // 浏览器禁用存储时仍保留当前会话内收起能力。
  }
}

function dynamicMaxWidth(containerWidth: number, minWidth: number, maxWidth: number): number {
  if (containerWidth <= 0) return maxWidth;
  return Math.max(minWidth, Math.min(maxWidth, Math.floor(containerWidth * WORKSPACE_SIDEBAR_MAX_RATIO)));
}

export function ResizableWorkspaceSidebar({
  children,
  ariaLabel,
  className,
  collapsedContent,
  side = 'left',
  minWidth: minWidthProp,
  maxWidth: maxWidthProp,
  defaultWidth: defaultWidthProp,
  storageKey: storageKeyProp,
  ...rest
}: ResizableWorkspaceSidebarProps) {
  const isRight = side === 'right';
  const minW = minWidthProp ?? WORKSPACE_SIDEBAR_MIN_WIDTH;
  const maxW = maxWidthProp ?? WORKSPACE_SIDEBAR_MAX_WIDTH;
  const defaultW = defaultWidthProp ?? WORKSPACE_SIDEBAR_DEFAULT_WIDTH;
  const storageK = storageKeyProp ?? WORKSPACE_SIDEBAR_STORAGE_KEY;
  const sidebarRef = useRef<HTMLElement | null>(null);
  const contentId = `workspace-sidebar-content-${useId().replace(/:/g, '')}`;
  const [preferredWidth, setPreferredWidth] = useState(() => readStoredWidth(storageK, defaultW, minW, maxW));
  const [collapsed, setCollapsed] = useState(() => (isRight ? false : readStoredCollapsed()));
  const preferredWidthRef = useRef(preferredWidth);
  const dragStartRef = useRef<{ clientX: number; width: number } | null>(null);
  const [maxWidth, setMaxWidth] = useState(maxW);
  const [dragging, setDragging] = useState(false);
  const width = clampWidth(preferredWidth, minW, maxWidth);
  const renderedWidth = collapsed ? WORKSPACE_SIDEBAR_COLLAPSED_WIDTH : width;

  const updatePreferredWidth = useCallback((nextWidth: number) => {
    preferredWidthRef.current = nextWidth;
    setPreferredWidth(nextWidth);
  }, []);

  useLayoutEffect(() => {
    // 注意：aside 必须直接挂载在布局容器（flex 行）内——父层宽度作为 45% 比例上限的基准。
    // 若被宽度=自身内容的包裹层包住，比例上限会被误钳到下限导致无法拖宽。
    const parent = sidebarRef.current?.parentElement;
    if (!parent) return;
    const updateMaxWidth = () => setMaxWidth(dynamicMaxWidth(parent.getBoundingClientRect().width, minW, maxW));
    updateMaxWidth();
    window.addEventListener('resize', updateMaxWidth);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(updateMaxWidth);
    observer?.observe(parent);
    return () => {
      window.removeEventListener('resize', updateMaxWidth);
      observer?.disconnect();
    };
    // 宽度组（minW/maxW）随 props 传入，运行期不切换，列全依赖即可
  }, [minW, maxW]);

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    dragStartRef.current = { clientX: event.clientX, width };
    setDragging(true);
  };

  const setAndPersistWidth = (nextWidth: number) => {
    const next = clampWidth(nextWidth, minW, maxWidth);
    updatePreferredWidth(next);
    persistWidth(storageK, next);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 24 : 8;
    let nextWidth: number | null = null;
    if (isRight) {
      // 右侧副屏：手柄在左缘，ArrowLeft/Up = 变宽，与左侧栏相反
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextWidth = width + step;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextWidth = width - step;
    } else {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') nextWidth = width - step;
      if (event.key === 'ArrowRight' || event.key === 'ArrowUp') nextWidth = width + step;
    }
    if (event.key === 'Home') nextWidth = minW;
    if (event.key === 'End') nextWidth = maxWidth;
    if (nextWidth === null) return;
    event.preventDefault();
    setAndPersistWidth(nextWidth);
  };

  const resetWidth = () => {
    updatePreferredWidth(defaultW);
    persistWidth(storageK, defaultW);
  };

  const toggleCollapsed = () => {
    const nextCollapsed = !collapsed;
    dragStartRef.current = null;
    setDragging(false);
    setCollapsed(nextCollapsed);
    persistCollapsed(nextCollapsed);
  };

  const onMove = (event: PointerEvent) => {
    const start = dragStartRef.current;
    if (!start) return;
    // 右侧副屏手柄在左缘：clientX 减小 = 变宽（方向与左侧栏相反）
    const delta = isRight ? start.clientX - event.clientX : event.clientX - start.clientX;
    updatePreferredWidth(clampWidth(start.width + delta, minW, maxWidth));
  };
  const onUp = () => {
    dragStartRef.current = null;
    setDragging(false);
    persistWidth(storageK, preferredWidthRef.current);
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
      style={{ width: `${renderedWidth}px` }}
      data-collapsed={collapsed}
      {...rest}
      className={cn(
        'relative shrink-0 flex-col overflow-visible border-border transition-[width] duration-300 ease-in-out',
        // 左侧栏沿用既有视觉（右缘分割线 + muted 底 + lg 以下隐藏）；右侧副屏左缘分割线、全屏段显示、底色随 className
        isRight ? 'flex border-l' : 'hidden border-r bg-muted/20 lg:flex',
        dragging && 'transition-none',
        className,
      )}
    >
      <div
        id={contentId}
        aria-hidden={collapsed}
        className={cn(
          'flex min-h-0 flex-1 flex-col overflow-hidden transition-opacity duration-200',
          collapsed && 'pointer-events-none invisible opacity-0',
        )}
      >
        {children}
      </div>

      {collapsed && <div className="absolute inset-0 flex overflow-hidden">{collapsedContent}</div>}

      {!isRight && (
        <TooltipProvider delayDuration={300}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label={collapsed ? '展开对话协作左栏' : '收起对话协作左栏'}
                aria-controls={contentId}
                aria-expanded={!collapsed}
                onClick={toggleCollapsed}
                className="absolute right-0 top-1/2 z-40 -mt-10 h-8 w-6 -translate-y-1/2 translate-x-1/2 rounded-full border border-border bg-background text-muted-foreground shadow-sm hover:border-primary hover:bg-background hover:text-primary"
              >
                {collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">{collapsed ? '展开左栏' : '收起左栏'}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}

      {!collapsed && (
        <div
          role="separator"
          aria-label={isRight ? `调整${ariaLabel}宽度` : '调整对话协作左栏宽度'}
          aria-orientation="vertical"
          aria-valuemin={minW}
          aria-valuemax={maxWidth}
          aria-valuenow={width}
          tabIndex={0}
          data-testid="workspace-sidebar-resizer"
          onPointerDown={handlePointerDown}
          onDoubleClick={resetWidth}
          onKeyDown={handleKeyDown}
          className={cn(
            'group/resizer absolute bottom-0 top-0 z-30 w-2.5 touch-none cursor-col-resize outline-none',
            isRight ? 'left-0 -translate-x-1/2' : 'right-0 translate-x-1/2',
          )}
        >
          {/* 常驻拖拽提示胶囊：垂直居中贴边缝，hover/focus/拖拽时转品牌色。 */}
          <span
            aria-hidden="true"
            data-testid="workspace-sidebar-grip"
            className={cn(
              'absolute left-1/2 top-1/2 flex h-8 w-6 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-sm transition-colors',
              dragging
                ? 'border-primary text-primary'
                : 'border-border group-hover/resizer:border-primary group-hover/resizer:text-primary group-focus-visible/resizer:border-primary group-focus-visible/resizer:text-primary',
            )}
          >
            <GripVertical className="h-3.5 w-3.5" />
          </span>
        </div>
      )}
    </aside>
  );
}
