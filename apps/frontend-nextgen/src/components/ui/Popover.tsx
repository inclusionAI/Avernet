import { cn } from '@/utils/cn';
import * as PopoverPrimitive from '@radix-ui/react-popover';
import React from 'react';

const Popover = PopoverPrimitive.Root;
const PopoverAnchor = PopoverPrimitive.Anchor;
const PopoverTrigger = PopoverPrimitive.Trigger;

/** Popover：用于轻量非模态内容，复杂表单应使用 Modal 或 Drawer。 */
const PopoverContent = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(({ className, align = 'center', sideOffset = 6, collisionPadding = 8, forceMount, ...props }, ref) => (
  // forceMount 需同时透传 Portal 与 Content,否则关闭态内容仍被整体卸载(Presence 在 Portal 这层)。
  <PopoverPrimitive.Portal forceMount={forceMount}>
    <PopoverPrimitive.Content
      ref={ref}
      forceMount={forceMount}
      align={align}
      sideOffset={sideOffset}
      collisionPadding={collisionPadding}
      className={cn(
        'z-[var(--z-popover)] w-72 rounded-md border border-border bg-popover p-4 text-popover-foreground shadow-md outline-none',
        'data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 data-[state=open]:zoom-in-95 data-[state=closed]:zoom-out-95',
        className,
      )}
      {...props}
    />
  </PopoverPrimitive.Portal>
));
PopoverContent.displayName = PopoverPrimitive.Content.displayName;

export { Popover, PopoverAnchor, PopoverContent, PopoverTrigger };
