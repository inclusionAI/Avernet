import { WORKSPACE_RIGHT_PANEL_LAYOUT } from '@/components/Workspace/workspacePanelLayout';
import type { GroupView, SessionView } from '@/domain/collaboration';
import type { PolicyResult } from '@/services/workspace/groupService';
import { toast } from 'sonner';
import { MembersPanel } from './MembersPanel';
import { WorkspaceOverlayPanel } from './WorkspaceOverlayPanel';

interface GroupMembersPanelSlotProps {
  open: boolean;
  group: GroupView;
  session: SessionView | null;
  canManage: PolicyResult;
  onClose: () => void;
}

export function GroupMembersPanelSlot({ open, group, session, canManage, onClose }: GroupMembersPanelSlotProps) {
  if (!open) return null;

  return (
    <WorkspaceOverlayPanel
      ariaLabel="群成员面板"
      onDismiss={onClose}
      {...WORKSPACE_RIGHT_PANEL_LAYOUT}
      className="border-l border-divider bg-card"
    >
      <MembersPanel
        group={group}
        session={session}
        canManage={canManage}
        onUpdateMode={() => toast.info('成员模式调整由协作群 Service 接入')}
        onRemoveParticipant={() => toast.info('移除成员由协作群 Service 接入')}
        onClose={onClose}
      />
    </WorkspaceOverlayPanel>
  );
}
