/**
 * MobileListContainer - 移动端列表容器
 *
 * 功能：
 * 1. 页面宽度 >= 375px 时，使用网格布局正常显示
 * 2. 页面宽度 < 375px 时，列表宽度 flex 自适应，但支持内部左右滚动
 *
 * 使用场景：
 * - Skill 市场列表
 * - MCP 市场列表
 * - 定时任务列表
 * - 任何横向卡片列表
 */
import { cn } from '@/utils/utils';
import React from 'react';

interface MobileListContainerProps {
  children: React.ReactNode;
  className?: string;
  /**
   * 列表项的最小宽度（在窄屏模式下每个卡片的宽度）
   * @default 280
   */
  itemMinWidth?: number;
  /**
   * 列表项间距（px）
   * @default 12
   */
  gap?: number;
  /**
   * 容器内边距（px）
   * @default 16
   */
  padding?: number;
}

/**
 * 移动端横向滚动列表容器
 *
 * 响应式行为：
 * - 屏幕宽度 >= 375px：[&>div]:contents 让子项被外层 grid 控制
 * - 屏幕宽度 < 375px：flex-nowrap + overflow-x-auto 实现横向滚动
 *
 * 使用示例：
 * ```tsx
 * <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
 *   <MobileListContainer itemMinWidth={280} gap={12}>
 *     {items.map(item => <Card key={item.id} {...item} />)}
 *   </MobileListContainer>
 * </div>
 * ```
 *
 * 注意：外层需要包裹 grid 容器用于 PC 端布局
 */
export function MobileListContainer({
  children,
  className,
  itemMinWidth = 280,
  gap = 12,
  padding = 16,
}: MobileListContainerProps) {
  return (
    <div
      className={cn(
        // 基础：让子项显示为内容（被外层 grid 控制）
        '[&>div]:contents',
        // 小于 375px：变为横向滚动容器
        'max-[374px]:[&>div]:flex max-[374px]:[&>div]:flex-nowrap',
        'max-[374px]:overflow-x-auto max-[374px]:scrollbar-hide',
        className,
      )}
      style={{
        // 小于 375px 时的样式
        gap: `${gap}px`,
        paddingLeft: `${padding}px`,
        paddingRight: `${padding}px`,
        WebkitOverflowScrolling: 'touch',
        scrollSnapType: 'x mandatory',
      }}
    >
      <div>
        {React.Children.map(children, (child, index) => (
          <div
            key={index}
            className={cn(
              // 默认：正常流中的元素
              // 小于 375px：固定宽度，不收缩，滚动吸附
              'max-[374px]:flex-shrink-0 max-[374px]:scroll-snap-start',
            )}
            style={{
              // 小于 375px 时的固定宽度
              minWidth: `${itemMinWidth}px`,
            }}
          >
            {child}
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * 简化版移动端横向滚动列表
 * 直接使用 flex 布局，不需要外层 grid
 *
 * 使用示例：
 * ```tsx
 * <MobileScrollList itemWidth={280} gap={12}>
 *   {items.map(item => <Card key={item.id} {...item} />)}
 * </MobileScrollList>
 * ```
 */
export function MobileScrollList({
  children,
  className,
  itemWidth = 280,
  gap = 12,
  padding = 16,
}: MobileListContainerProps & { itemWidth?: number }) {
  return (
    <div
      className={cn('w-full overflow-x-auto scrollbar-hide', className)}
      style={{
        paddingLeft: `${padding}px`,
        paddingRight: `${padding}px`,
        WebkitOverflowScrolling: 'touch',
        scrollSnapType: 'x mandatory',
      }}
    >
      <div
        className="flex flex-nowrap"
        style={{
          gap: `${gap}px`,
        }}
      >
        {React.Children.map(children, (child, index) => (
          <div
            key={index}
            className="flex-shrink-0 scroll-snap-start"
            style={{
              width: `${itemWidth}px`,
            }}
          >
            {child}
          </div>
        ))}
      </div>
    </div>
  );
}

export default MobileListContainer;
