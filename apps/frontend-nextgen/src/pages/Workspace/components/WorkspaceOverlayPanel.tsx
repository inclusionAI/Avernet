import { Button } from '@/components/ui';
import { useWorkspaceOverlayBounds } from '@/hooks/useWorkspaceOverlayBounds';
import { cn } from '@/utils/cn';
import type { ComponentProps } from 'react';
import { createPortal } from 'react-dom';
import { ResizableWorkspaceSidebar } from './ResizableWorkspaceSidebar';

type WorkspaceOverlayPanelProps = Omit<ComponentProps<typeof ResizableWorkspaceSidebar>, 'side' | 'onDismiss'> & {
  onDismiss: () => void;
};

/** Portal 到顶层并按会话区定位：遮罩同时覆盖聊天和持久副屏，不卸载任何业务面板。 */
export function WorkspaceOverlayPanel({ ariaLabel, className, onDismiss, ...props }: WorkspaceOverlayPanelProps) {
  const { anchorRef, bounds } = useWorkspaceOverlayBounds();
  return (
    <>
      <span ref={anchorRef} hidden aria-hidden="true" />
      {bounds &&
        createPortal(
          <div className="fixed z-40 flex justify-end overflow-hidden" style={bounds} data-workspace-overlay="true">
            <Button
              type="button"
              variant="ghost"
              aria-label={`关闭${ariaLabel}`}
              className="absolute inset-0 h-full w-full cursor-default rounded-none bg-background/40 p-0 backdrop-blur-sm hover:bg-background/40"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={onDismiss}
            />
            <ResizableWorkspaceSidebar
              {...props}
              ariaLabel={ariaLabel}
              side="right"
              onDismiss={onDismiss}
              className={cn('workspace-overlay-panel z-10 max-w-full bg-card shadow-xl', className)}
            />
          </div>,
          document.body,
        )}
    </>
  );
}
