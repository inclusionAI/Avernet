/**
 * Sidebar - 通用侧边栏容器组件
 *
 * 职责：
 * - 提供侧边栏的外壳样式（背景、边框、宽度）
 * - 支持受控的展开 / 收起，收起后可渲染图标列
 * - 支持拖拽调节宽度
 * - 不关心内部业务内容
 *
 * 使用示例：
 *
 * // 纯样式容器（无收起功能）
 * <Sidebar width={{ default: 340 }}>...</Sidebar>
 *
 * // 带收起的完整用法
 * <Sidebar
 *   width={{ default: 240, collapsed: 56, min: 200, max: 400 }}
 *   collapsed={collapsed}
 *   onCollapsed={setCollapsed}
 *   collapsedContent={<MyIconColumn />}
 * >
 *   ...
 * </Sidebar>
 *
 * // 可调节宽度
 * <Sidebar
 *   width={{ default: 240, min: 180, max: 500 }}
 *   resizable
 *   onWidthChange={(width) => console.log('当前宽度:', width)}
 * >
 *   ...
 * </Sidebar>
 */

import { cn } from '@/utils/utils';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';

export interface SidebarProps {
  /**
   * 宽度配置（px 数值）。
   * default: 展开时的宽度，默认 240。
   * collapsed: 收起时的宽度，默认 56，仅在传入 onCollapsed 时有效。
   * min: 最小宽度，默认 180。
   * max: 最大宽度，默认 600。
   */
  width?: {
    default?: number;
    collapsed?: number;
    min?: number;
    max?: number;
  };

  /**
   * 收起状态（受控）。
   * 不传则 Sidebar 始终展开，不显示折叠按钮。
   */
  collapsed?: boolean;

  /**
   * 收起状态变更回调。
   * 传入后才会显示折叠按钮。
   */
  onCollapsed?: (collapsed: boolean) => void;

  /**
   * 收起时渲染的内容（通常为图标列）。
   * 不传则收起时内容区域直接隐藏。
   */
  collapsedContent?: React.ReactNode;

  /** 侧边栏主内容（展开时显示） */
  children: React.ReactNode;

  className?: string;

  /**
   * 是否支持拖拽调节宽度
   */
  resizable?: boolean;

  /**
   * 宽度变化回调
   */
  onWidthChange?: (width: number) => void;

  /**
   * 是否记住宽度到 localStorage
   */
  rememberWidth?: boolean;

  /**
   * localStorage key（rememberWidth 为 true 时使用）
   */
  storageKey?: string;

  /**
   * 侧边栏位置：'left' 表示左侧边栏（拖拽手柄在右侧），'right' 表示右侧边栏（拖拽手柄在左侧）
   * 默认 'left'
   */
  position?: 'left' | 'right';

  /**
   * 隐藏内置的折叠按钮（仍支持收起，由外部自行提供触发入口）。
   * 默认 false。
   */
  hideToggleButton?: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({
  width,
  collapsed = false,
  onCollapsed,
  collapsedContent,
  children,
  className,
  resizable = false,
  onWidthChange,
  rememberWidth = false,
  storageKey = 'sidebar-width',
  position = 'left',
  hideToggleButton = false,
}) => {
  const isRight = position === 'right';
  const defaultWidth = width?.default ?? 240;
  const collapsedWidth = width?.collapsed ?? 44;
  const minWidth = width?.min ?? 180;
  const maxWidth = width?.max ?? 600;
  const collapsible = onCollapsed !== undefined;
  const isCollapsed = collapsible && collapsed;

  // 从 localStorage 读取保存的宽度
  const getInitialWidth = useCallback(() => {
    if (rememberWidth && typeof window !== 'undefined') {
      try {
        const saved = localStorage.getItem(storageKey);
        if (saved) {
          const parsed = parseInt(saved, 10);
          if (!isNaN(parsed) && parsed >= minWidth && parsed <= maxWidth) {
            return parsed;
          }
        }
      } catch {
        // 忽略 localStorage 错误
      }
    }
    return defaultWidth;
  }, [rememberWidth, storageKey, minWidth, maxWidth, defaultWidth]);

  const [currentWidth, setCurrentWidth] = useState(getInitialWidth);
  const [isResizing, setIsResizing] = useState(false);
  const sidebarRef = useRef<HTMLDivElement>(null);
  const startXRef = useRef(0);
  const startWidthRef = useRef(currentWidth);

  // 拖拽开始
  const handleResizeStart = useCallback(
    (e: React.MouseEvent | React.TouchEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsResizing(true);
      startXRef.current = 'touches' in e ? e.touches[0].clientX : e.clientX;
      startWidthRef.current = currentWidth;

      // 禁用文本选择
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'ew-resize';
    },
    [currentWidth],
  );

  // 拖拽中
  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent | TouchEvent) => {
      const clientX = 'touches' in e ? e.touches[0].clientX : e.clientX;
      // 左侧边栏：向右拖拽增加宽度，右侧边栏：向左拖拽增加宽度
      const deltaX = isRight
        ? startXRef.current - clientX
        : clientX - startXRef.current;
      const newWidth = Math.max(
        minWidth,
        Math.min(maxWidth, startWidthRef.current + deltaX),
      );
      setCurrentWidth(newWidth);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';

      // 保存到 localStorage
      if (rememberWidth && typeof window !== 'undefined') {
        try {
          localStorage.setItem(storageKey, String(currentWidth));
        } catch {
          // 忽略 localStorage 错误
        }
      }

      // 触发回调
      onWidthChange?.(currentWidth);
    };

    const handleTouchMove = (e: TouchEvent) => {
      if (isResizing) {
        e.preventDefault();
      }
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.addEventListener('touchmove', handleTouchMove, { passive: false });
    document.addEventListener('touchend', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.removeEventListener('touchmove', handleTouchMove);
      document.removeEventListener('touchend', handleMouseUp);
    };
  }, [
    isResizing,
    minWidth,
    maxWidth,
    currentWidth,
    rememberWidth,
    storageKey,
    onWidthChange,
  ]);

  // 计算实际显示的宽度
  const displayWidth = isCollapsed ? collapsedWidth : currentWidth;

  return (
    <>
      <div
        ref={sidebarRef}
        style={{ width: displayWidth }}
        className={cn(
          'relative h-full flex flex-col flex-shrink-0',
          'bg-white',
          // 边框方向根据 position 确定
          isRight
            ? 'border-l border-slate-200/60'
            : 'border-r border-slate-200/60',
          'transition-all duration-300',
          isResizing && 'transition-none',
          className,
        )}
      >
        {/* 折叠按钮（仅 collapsible 且不在拖拽时显示） */}
        {collapsible && !isResizing && !hideToggleButton && (
          <button
            type="button"
            onClick={() => onCollapsed(!collapsed)}
            data-aspm-click="ca114860.da193909"
            data-aspm-desc="Sidebar-折叠按钮"
            data-aspm-param={``}
            data-aspm-expo
            className={cn(
              'absolute top-1/2 -translate-y-1/2 z-40 flex items-center justify-center w-4 h-9 bg-white border border-slate-200 rounded-full shadow-sm hover:border-lavender-300 hover:text-lavender-600 text-slate-400 transition-colors',
              // 折叠按钮位置根据 position 确定
              isRight ? '-left-3' : '-right-3',
            )}
          >
            {/* 折叠按钮图标根据 position 和 collapsed 状态确定 */}
            {isRight ? (
              isCollapsed ? (
                <ChevronLeft className="w-4 h-4" />
              ) : (
                <ChevronRight className="w-4 h-4" />
              )
            ) : isCollapsed ? (
              <ChevronRight className="w-4 h-4" />
            ) : (
              <ChevronLeft className="w-4 h-4" />
            )}
          </button>
        )}

        {/* 拖拽手柄（仅 resizable 且未收起时显示） */}
        {resizable && !isCollapsed && (
          <>
            {/* 拖拽区域 - 加宽到 w-6 并提升 z-index 确保可见 */}
            <div
              onMouseDown={handleResizeStart}
              onTouchStart={handleResizeStart}
              className={cn(
                'absolute top-0 w-2 h-full cursor-ew-resize z-30',
                'flex items-center justify-center',
                'hover:bg-lavender-100/50',
                isResizing && 'bg-lavender-100/80',
                // 拖拽手柄位置根据 position 确定
                isRight ? 'left-0' : 'right-0',
              )}
              title="拖拽调节宽度"
            >
              {/* 拖拽指示条 - 默认隐藏，hover 时显示 */}
              <div
                className={cn(
                  'w-1 h-16 rounded-full transition-all duration-200',
                  isResizing
                    ? 'bg-lavender-500'
                    : 'bg-slate-300/0 hover:bg-slate-300/80',
                )}
              />
            </div>

            {/* 宽度指示器（拖拽时显示） */}
            {isResizing && (
              <div className="absolute -right-16 top-1/2 -translate-y-1/2 z-30 px-2 py-1 bg-slate-800 text-white text-xs rounded">
                {currentWidth}px
              </div>
            )}
          </>
        )}

        {/* 收起时：图标列 */}
        {isCollapsed && collapsedContent && (
          <div className="flex flex-col items-center gap-2 pt-3 px-2 flex-1 overflow-y-auto">
            {collapsedContent}
          </div>
        )}

        {/* 展开时：完整内容 */}
        {!isCollapsed && (
          <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
            {children}
          </div>
        )}
      </div>

      {/* 遮罩层（拖拽时显示，防止 iframe 等元素捕获事件） */}
      {isResizing && <div className="fixed inset-0 z-50 cursor-ew-resize" />}
    </>
  );
};

export default Sidebar;
