import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import type { ComponentProps } from 'react';
import { ResizableWorkspaceSidebar } from './ResizableWorkspaceSidebar';

type WorkspaceOverlayPanelProps = Omit<ComponentProps<typeof ResizableWorkspaceSidebar>, 'side' | 'onDismiss'> & {
  onDismiss: () => void;
};

/** Mount inside the positioned conversation region, alongside the chat and SDK secondary screen. */
export function WorkspaceOverlayPanel({ ariaLabel, className, onDismiss, ...props }: WorkspaceOverlayPanelProps) {
  return (
    <div className="absolute inset-0 z-40 flex justify-end overflow-hidden" data-workspace-overlay="true">
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
    </div>
  );
}
