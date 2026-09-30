import { cn } from '@/utils/cn';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import React from 'react';
import { Button } from './Button';

const Drawer = DialogPrimitive.Root;
const DrawerTrigger = DialogPrimitive.Trigger;
const DrawerClose = DialogPrimitive.Close;

type DrawerSide = 'left' | 'right' | 'bottom';
type DrawerSize = 'sm' | 'md' | 'lg' | 'full';

interface DrawerContentProps extends React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  side?: DrawerSide;
  size?: DrawerSize;
  showOverlay?: boolean;
  showClose?: boolean;
  closeLabel?: string;
  /** 内层滚动容器的 className 覆盖（如 `p-0` 全出血、`flex flex-col` 让内容自管布局）。
   *  tailwind-merge 会合并冲突项：传 `p-0` 会覆盖默认 `p-6`。 */
  bodyClassName?: string;
}

const sizeClasses: Record<DrawerSide, Record<DrawerSize, string>> = {
  left: { sm: 'w-80', md: 'w-[28rem]', lg: 'w-[40rem]', full: 'w-[min(100vw,64rem)]' },
  right: { sm: 'w-80', md: 'w-[28rem]', lg: 'w-[40rem]', full: 'w-[min(100vw,64rem)]' },
  bottom: { sm: 'max-h-[40vh]', md: 'max-h-[60vh]', lg: 'max-h-[80vh]', full: 'max-h-[calc(100vh-1rem)]' },
};

/** Drawer：基于 Dialog 的侧边/底部抽屉，尺寸使用有限枚举。 */
const DrawerContent = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Content>, DrawerContentProps>(
  (
    {
      className,
      children,
      side = 'right',
      size = 'md',
      showOverlay = true,
      showClose = true,
      closeLabel = '关闭抽屉',
      bodyClassName,
      ...props
    },
    ref,
  ) => {
    const isBottom = side === 'bottom';
    return (
      <DialogPrimitive.Portal>
        {showOverlay ? (
          <DialogPrimitive.Overlay className="fixed inset-0 z-[var(--z-drawer)] bg-black/40 backdrop-blur-sm" />
        ) : null}
        <DialogPrimitive.Content
          ref={ref}
          className={cn(
            'fixed z-[var(--z-drawer)] flex flex-col border-border bg-background text-foreground shadow-xl outline-none',
            isBottom
              ? 'inset-x-0 bottom-0 rounded-t-xl border-t'
              : `inset-y-0 ${side === 'left' ? 'left-0 border-r' : 'right-0 border-l'} ${sizeClasses[side][size]}`,
            isBottom ? sizeClasses.bottom[size] : '',
            'data-[state=open]:animate-in data-[state=closed]:animate-out duration-300',
            isBottom
              ? 'data-[state=open]:slide-in-from-bottom data-[state=closed]:slide-out-to-bottom'
              : side === 'left'
              ? 'data-[state=open]:slide-in-from-left data-[state=closed]:slide-out-to-left'
              : 'data-[state=open]:slide-in-from-right data-[state=closed]:slide-out-to-right',
            className,
          )}
          {...props}
        >
          {showClose ? (
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" className="absolute right-3 top-3" aria-label={closeLabel}>
                <X aria-hidden className="size-4" />
              </Button>
            </DialogPrimitive.Close>
          ) : null}
          <div
            className={cn(
              'min-h-0 flex-1 overflow-y-auto p-6',
              // 关闭按钮 absolute right-3 top-3 + h-9 占据顶部 12~48px,默认正文下移 56px 避开重叠;
              // bodyClassName 在其后,调用方传 p-0 / pt-* 仍可覆盖。
              showClose && 'pt-14',
              bodyClassName,
            )}
          >
            {children}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    );
  },
);
DrawerContent.displayName = 'DrawerContent';

const DrawerHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn('mb-4 flex flex-col gap-2', className)} {...props} />
);
const DrawerFooter = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn('mt-4 flex justify-end gap-2', className)} {...props} />
);
const DrawerTitle = DialogPrimitive.Title;
const DrawerDescription = DialogPrimitive.Description;

export {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
};
