import { cn } from '@/utils/cn';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import React from 'react';

/** PaneDialog:portal 挂载进指定容器、absolute 定位覆于窗格之上的窗格级 Dialog。
 *  与 Modal/Drawer（视口级 fixed）相对,遮罩/内容只占据调用方给定的容器窗格,
 *  几何走向（底部 sheet / 侧边导轨等）由调用方通过 className 描述。
 *  Portal 的 container 必传或延后设置:undefined 时 Radix 会退回 document.body,
 *  那会退化成视口级语义,调用方需在 onOpenChange 时解析好容器。 */
const PaneDialog = DialogPrimitive.Root;
const PaneDialogTrigger = DialogPrimitive.Trigger;
const PaneDialogClose = DialogPrimitive.Close;

type PaneDialogPortalProps = React.ComponentPropsWithoutRef<typeof DialogPrimitive.Portal>;

/** 窗格级 Portal:container 之外其余 props 原样透传,语义与 Radix 一致。 */
const PaneDialogPortal = ({ container, children, ...props }: PaneDialogPortalProps) => (
  <DialogPrimitive.Portal container={container} {...props}>
    {children}
  </DialogPrimitive.Portal>
);

const PaneDialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay ref={ref} className={cn('absolute inset-0 z-40 bg-foreground/10', className)} {...props} />
));
PaneDialogOverlay.displayName = 'PaneDialogOverlay';

const PaneDialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <DialogPrimitive.Content
    ref={ref}
    // 只锚窗格级基座;inset/宽度等具体几何由调用方 className 描述。
    className={cn('absolute z-50 flex flex-col bg-background text-foreground outline-none', className)}
    {...props}
  >
    {children}
  </DialogPrimitive.Content>
));
PaneDialogContent.displayName = 'PaneDialogContent';

const PaneDialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title ref={ref} className={cn('text-base font-semibold', className)} {...props} />
));
PaneDialogTitle.displayName = 'PaneDialogTitle';

const PaneDialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description ref={ref} className={cn('text-xs text-muted-foreground', className)} {...props} />
));
PaneDialogDescription.displayName = 'PaneDialogDescription';

export {
  PaneDialog,
  PaneDialogClose,
  PaneDialogContent,
  PaneDialogDescription,
  PaneDialogOverlay,
  PaneDialogPortal,
  PaneDialogTitle,
  PaneDialogTrigger,
};
