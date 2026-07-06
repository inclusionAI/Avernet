import { cn } from '@/utils/utils';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import * as React from 'react';

const Drawer = DialogPrimitive.Root;
const DrawerClose = DialogPrimitive.Close;
const DrawerPortal = DialogPrimitive.Portal;

function isFromGlobalErrorPrompt(event: Event) {
  const target = event.target;
  return (
    target instanceof Element &&
    Boolean(target.closest('[data-global-error-prompt]'))
  );
}

interface DrawerContentProps
  extends React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  width?: number | string;
  minWidth?: number | string;
  height?: number | string;
  title?: React.ReactNode;
  overlay?: boolean;
  position?: 'left' | 'right' | 'bottom';
  /** 隐藏 header 与 body 之间那条分割线（默认显示，保持向后兼容） */
  hideHeaderBorder?: boolean;
}

const DrawerContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  DrawerContentProps
>(
  (
    {
      className,
      width = 480,
      minWidth,
      height,
      title,
      overlay = false,
      position = 'right',
      hideHeaderBorder = false,
      children,
      onInteractOutside,
      ...props
    },
    ref,
  ) => {
    const isLeft = position === 'left';
    const isBottom = position === 'bottom';
    const handleInteractOutside: React.ComponentPropsWithoutRef<
      typeof DialogPrimitive.Content
    >['onInteractOutside'] = (event) => {
      const originalEvent = event.detail.originalEvent;
      if (isFromGlobalErrorPrompt(originalEvent)) {
        event.preventDefault();
        return;
      }
      onInteractOutside?.(event);
    };

    // 底部抽屉样式
    if (isBottom) {
      return (
        <DrawerPortal>
          {overlay && (
            <DialogPrimitive.Overlay
              style={{ zIndex: 299 }}
              className="fixed inset-0 bg-black/25 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0"
            />
          )}
          <DialogPrimitive.Content
            ref={ref}
            style={{ height, zIndex: 300 }}
            className={cn(
              'fixed bottom-0 left-0 right-0 flex flex-col bg-white shadow-[0_-8px_30px_rgba(0,0,0,0.12)] rounded-t-2xl',
              'border-t border-slate-100',
              'data-[state=open]:animate-in data-[state=closed]:animate-out',
              'data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom',
              'duration-300 ease-out',
              className,
            )}
            onInteractOutside={handleInteractOutside}
            {...props}
          >
            {/* Header - 底部选择器的标题栏 */}
            <div
              className={cn(
                'flex items-center justify-between px-4 py-3.5 flex-shrink-0',
                !hideHeaderBorder && 'border-b border-slate-100',
              )}
            >
              <DialogPrimitive.Title className="text-[15px] font-medium text-slate-800">
                {title}
              </DialogPrimitive.Title>
              <DialogPrimitive.Close className="rounded-lg p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 active:bg-slate-200 transition-colors">
                <X size={18} />
              </DialogPrimitive.Close>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto">{children}</div>
          </DialogPrimitive.Content>
        </DrawerPortal>
      );
    }

    // 左侧/右侧抽屉样式
    return (
      <DrawerPortal>
        {overlay && (
          <DialogPrimitive.Overlay
            style={{ zIndex: 299 }}
            className="fixed inset-0 bg-black/25 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0"
          />
        )}
        <DialogPrimitive.Content
          ref={ref}
          style={{ width, minWidth, zIndex: 300 }}
          className={cn(
            'fixed top-0 bottom-0 flex flex-col bg-white shadow-[0_0_40px_rgba(0,0,0,0.1)]',
            isLeft
              ? 'left-0 border-r border-slate-100 data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left'
              : 'right-0 border-l border-slate-100 data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'duration-300 ease-out',
            className,
          )}
          onInteractOutside={handleInteractOutside}
          {...props}
        >
          {/* Header */}
          <div
            className={cn(
              'flex items-center justify-between px-5 py-4 flex-shrink-0',
              !hideHeaderBorder && 'border-b border-slate-100',
            )}
          >
            <DialogPrimitive.Title className="text-[15px] font-medium text-slate-800 w-full">
              {title}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close className="rounded-lg p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 active:bg-slate-200 transition-colors">
              <X size={18} />
            </DialogPrimitive.Close>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto">{children}</div>
        </DialogPrimitive.Content>
      </DrawerPortal>
    );
  },
);
DrawerContent.displayName = 'DrawerContent';

export { Drawer, DrawerContent, DrawerClose };
