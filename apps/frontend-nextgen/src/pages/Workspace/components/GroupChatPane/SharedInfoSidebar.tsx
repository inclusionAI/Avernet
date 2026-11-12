import { WORKSPACE_RIGHT_PANEL_LAYOUT } from '@/components/Workspace/workspacePanelLayout';
import type { GroupContextView } from '@/services/backendApi/collaboration/groupContextController';
import { ManagePanelHeader } from '../ManagePanel/ManagePanelHeader';
import { SharedInfoList } from '../ManagePanel/SharedInfoList';
import { ResizableWorkspaceSidebar } from '../ResizableWorkspaceSidebar';

interface SharedInfoSidebarProps {
  open: boolean;
  view: GroupContextView;
  viewerId?: string | null;
  names: ReadonlyMap<string, string>;
  onClose: () => void;
}

export function SharedInfoSidebar({ open, view, viewerId, names, onClose }: SharedInfoSidebarProps) {
  return (
    <ResizableWorkspaceSidebar
      ariaLabel="共享信息"
      side="right"
      open={open}
      onDismiss={open ? onClose : undefined}
      {...WORKSPACE_RIGHT_PANEL_LAYOUT}
      className="bg-background"
    >
      {open && (
        <div className="flex h-full flex-col">
          <ManagePanelHeader title="共享信息" subtitle="仅展示您有权查看的共享信息" closeLabel="关闭共享信息" onClose={onClose} />
          <div className="app-scrollbar flex-1 overflow-y-auto p-4">
            <SharedInfoList view={view} viewerId={viewerId} names={names} />
          </div>
        </div>
      )}
    </ResizableWorkspaceSidebar>
  );
}
