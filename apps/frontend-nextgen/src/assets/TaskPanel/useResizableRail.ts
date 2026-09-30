// @asset-migrated: teamclaw 自研资产
/**
 * useResizableRail —— 任务副屏「节点下钻左侧 rail」与主面板之间的可拖拽宽度调节。
 *
 * - 拖动分隔条按 pointer 位置实时设置 railWidth，并按容器宽度的 RAIL_MAX_RATIO 上限与 RAIL_MIN_WIDTH
 *   下限做钳制；拖动期间禁用正文选区、切换 col-resize 光标，避免拖动时选中文字。
 * - 宽度写入 localStorage（key: task-panel-drill-rail-width），刷新/重开仍沿用上次宽度。
 * - 返回 startResize（挂到分隔条 onPointerDown）、containerRef（挂到 row-flex 容器以取边界算钳制）、
 *   以及 min/maxRatio/defaultWidth 供键盘调节与双击恢复使用。
 */
import type { PointerEvent as ReactPointerEvent, RefObject } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';

export const RAIL_MIN_WIDTH = 300;
export const RAIL_DEFAULT_WIDTH = 360;
export const RAIL_MAX_RATIO = 0.7;
const RAIL_WIDTH_STORAGE_KEY = 'task-panel-drill-rail-width';

export interface ResizableRail {
  railWidth: number;
  setRailWidth: React.Dispatch<React.SetStateAction<number>>;
  containerRef: RefObject<HTMLDivElement>;
  startResize: (event: ReactPointerEvent) => void;
  min: number;
  maxRatio: number;
  defaultWidth: number;
}

export function useResizableRail(): ResizableRail {
  const [railWidth, setRailWidth] = useState<number>(() => {
    if (typeof window === 'undefined') return RAIL_DEFAULT_WIDTH;
    const saved = Number(window.localStorage.getItem(RAIL_WIDTH_STORAGE_KEY));
    return Number.isFinite(saved) && saved >= RAIL_MIN_WIDTH ? saved : RAIL_DEFAULT_WIDTH;
  });
  const containerRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);

  const handlePointerMove = useCallback((event: PointerEvent) => {
    if (!draggingRef.current) return;
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const max = Math.floor(rect.width * RAIL_MAX_RATIO);
    setRailWidth(Math.min(Math.max(RAIL_MIN_WIDTH, event.clientX - rect.left), max));
  }, []);

  const handlePointerUp = useCallback(() => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
    window.removeEventListener('pointermove', handlePointerMove);
    window.removeEventListener('pointerup', handlePointerUp);
  }, [handlePointerMove]);

  const startResize = useCallback(
    (event: ReactPointerEvent) => {
      event.preventDefault();
      draggingRef.current = true;
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'col-resize';
      window.addEventListener('pointermove', handlePointerMove);
      window.addEventListener('pointerup', handlePointerUp);
    },
    [handlePointerMove, handlePointerUp],
  );

  // 持久化宽度（拖动中每帧写入开销可接受，且保证刷新即用最新值）。
  useEffect(() => {
    try {
      window.localStorage.setItem(RAIL_WIDTH_STORAGE_KEY, String(railWidth));
    } catch {
      /* localStorage 不可用（隐私模式/超额）时静默忽略 */
    }
  }, [railWidth]);

  // 组件卸载时清理可能残留的拖拽监听，避免悬挂引用。
  useEffect(() => {
    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
  }, [handlePointerMove, handlePointerUp]);

  return {
    railWidth,
    setRailWidth,
    containerRef,
    startResize,
    min: RAIL_MIN_WIDTH,
    maxRatio: RAIL_MAX_RATIO,
    defaultWidth: RAIL_DEFAULT_WIDTH,
  };
}
