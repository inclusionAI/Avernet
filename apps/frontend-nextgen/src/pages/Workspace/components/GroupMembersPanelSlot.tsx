import { WORKSPACE_RIGHT_PANEL_LAYOUT } from '@/components/Workspace/workspacePanelLayout';
import type { GroupView, SessionView } from '@/domain/collaboration';
import type { PolicyResult } from '@/services/workspace/groupService';
import { toast } from 'sonner';
import { MembersPanel } from './MembersPanel';
import { ResizableWorkspaceSidebar } from './ResizableWorkspaceSidebar';

interface GroupMembersPanelSlotProps {
  open: boolean;
  group: GroupView;
  session: SessionView | null;
  canManage: PolicyResult;
  onClose: () => void;
}

/** 群成员内联副屏（与 chat 页右缘面板同形）：aside 常挂、open 开合，本体仅展开态渲染。 */
export function GroupMembersPanelSlot({ open, group, session, canManage, onClose }: GroupMembersPanelSlotProps) {
  if (!group) return null;

  return (
    <ResizableWorkspaceSidebar
      ariaLabel="群成员面板"
      side="right"
      open={open}
      onDismiss={open ? onClose : undefined}
      {...WORKSPACE_RIGHT_PANEL_LAYOUT}
      className="border-l border-divider bg-card"
    >
      {open ? (
        <MembersPanel
          group={group}
          session={session}
          canManage={canManage}
          onUpdateMode={() => toast.info('成员模式调整由协作群 Service 接入')}
          onRemoveParticipant={() => toast.info('移除成员由协作群 Service 接入')}
          onClose={onClose}
        />
      ) : null}
    </ResizableWorkspaceSidebar>
  );
}
