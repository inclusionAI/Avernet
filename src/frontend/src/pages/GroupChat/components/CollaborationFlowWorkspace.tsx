import { Button } from '@/components';
import { cn } from '@/utils/utils';
import { Workflow, X } from 'lucide-react';
import React from 'react';

interface CollaborationFlowWorkspaceProps {
  children: React.ReactNode;
  panel?: React.ReactNode;
  className?: string;
  compact: boolean;
  compactPanelOpen: boolean;
  onCompactPanelOpenChange: (open: boolean) => void;
  panelMeta?: React.ReactNode;
}

const CollaborationFlowWorkspace: React.FC<CollaborationFlowWorkspaceProps> = ({
  children,
  panel,
  className,
  compact,
  compactPanelOpen,
  onCompactPanelOpenChange,
  panelMeta,
}) => {
  const hasPanel = panel !== null && panel !== undefined;

  const panelBody = (
    <>
      <div className="flex min-h-11 flex-shrink-0 items-center gap-2 border-b border-slate-200/60 px-3">
        <Workflow className="h-4 w-4 text-slate-500" aria-hidden="true" />
        <span className="text-sm font-medium text-slate-700">协作流程</span>
        <div className="ml-auto flex items-center gap-2">
          {panelMeta}
          <Button
            variant="default"
            ghost
            size="icon"
            className={cn('h-7 w-7', !compact && 'hidden')}
            aria-label="关闭协作流程"
            onClick={() => onCompactPanelOpenChange(false)}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div className="flex min-h-0 flex-1">{panel}</div>
    </>
  );

  return (
    <div
      data-collaboration-flow-layout={compact ? 'compact' : 'split'}
      className={cn(
        'relative flex min-h-0 flex-1 overflow-hidden rounded-xl bg-slate-100/70',
        className,
      )}
    >
      <div
        data-collaboration-flow-primary
        className={cn(
          'flex min-w-0 flex-col',
          hasPanel && !compact ? 'w-[42rem] flex-none' : 'w-full flex-1',
        )}
      >
        {children}
      </div>

      {hasPanel && !compact && (
        <div aria-hidden="true" className="w-px flex-none bg-slate-200/70" />
      )}

      {hasPanel && (!compact || compactPanelOpen) && (
        <aside
          aria-label="协作流程副屏"
          className={cn(
            'flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-white',
            compact && 'absolute inset-0 z-20 w-full shadow-xl',
          )}
        >
          {panelBody}
        </aside>
      )}
    </div>
  );
};

export default CollaborationFlowWorkspace;
