import { cn } from '@/utils/cn';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import React from 'react';

/** Tooltip 基础组件：统一 hover/focus 提示，不替代控件的 accessible name。 */
const TooltipProvider = TooltipPrimitive.Provider;
const Tooltip = TooltipPrimitive.Root;
const TooltipTrigger = TooltipPrimitive.Trigger;

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 6, ...props }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        'z-[var(--z-tooltip)] overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2.5 py-1.5 text-xs text-[var(--color-fg)] shadow-lg',
        'selection:bg-[var(--color-primary-soft)] selection:text-[var(--color-fg)]',
        // 无退出动画（缺陷修复）：hover 显隐行内按钮（hidden/group-hover）的场景下，
        // 行失焦瞬间按钮 display:none 使锚点矩形归零，退出动画残留的气泡会被重定位到
        // 页面左上角闪烁。关闭即同步卸载（同帧完成，先于锚点隐藏的绘制），杜绝孤儿气泡。
        'data-[state=open]:animate-in data-[state=open]:fade-in-0',
        className,
      )}
      {...props}
    />
  </TooltipPrimitive.Portal>
));
TooltipContent.displayName = TooltipPrimitive.Content.displayName;

export { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger };
