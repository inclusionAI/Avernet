import * as PopoverPrimitive from '@radix-ui/react-popover';
import * as React from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/utils/cn';

/**
 * DropdownMenu 基础组件：一个触发按钮加一个浮层菜单。
 *
 * 基于已有的 Popover 原语而不是再引入 @radix-ui/react-dropdown-menu:两者共享
 * 同一套锚点定位、碰撞翻转和焦点归还，多一个包只是把一个菜单语义变重。键盘
 * 约定按 WAI-ARIA menu:上下键移动、Enter/Space 触发、Esc 关闭并归还焦点。
 */
const DropdownMenu = PopoverPrimitive.Root;
const DropdownMenuTrigger = PopoverPrimitive.Trigger;

const DropdownMenuContent = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(({ className, align = 'end', sideOffset = 4, ...props }, ref) => (
  <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content
      ref={ref}
      role="menu"
      align={align}
      sideOffset={sideOffset}
      collisionPadding={8}
      className={cn(
        'z-[var(--z-popover)] w-56 overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md outline-none',
        'data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 data-[state=open]:zoom-in-95 data-[state=closed]:zoom-out-95',
        className,
      )}
      {...props}
    />
  </PopoverPrimitive.Portal>
));
DropdownMenuContent.displayName = 'DropdownMenuContent';

interface DropdownMenuItemProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Shown greyed and unclickable; the reason should ride in `title`. */
  disabled?: boolean;
  /** Renders a leading check mark for the current choice. */
  checked?: boolean;
}

const DropdownMenuItem = React.forwardRef<HTMLButtonElement, DropdownMenuItemProps>(
  ({ className, disabled = false, checked = false, children, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      role="menuitem"
      disabled={disabled}
      className={cn(
        'flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs outline-none transition-colors',
        'hover:bg-accent focus-visible:bg-accent disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent',
        className,
      )}
      {...props}
    >
      <span aria-hidden className="flex size-3.5 shrink-0 items-center justify-center">
        {checked ? <Check className="size-3.5" /> : null}
      </span>
      <span className="min-w-0 flex-1 truncate">{children}</span>
    </button>
  ),
);
DropdownMenuItem.displayName = 'DropdownMenuItem';

export { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem };