import type { GroupView, SessionView } from '@/domain/collaboration';
import type { PolicyResult } from '@/services/workspace/groupService';
import { toast } from 'sonner';
import { MembersPanel } from './MembersPanel';

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
    <div className="flex w-[min(320px,30vw)] max-w-[30vw] shrink-0 border-l border-border">
      <MembersPanel
        group={group}
        session={session}
        canManage={canManage}
        onUpdateMode={() => toast.info('成员模式调整由协作群 Service 接入')}
        onRemoveParticipant={() => toast.info('移除成员由协作群 Service 接入')}
        onClose={onClose}
      />
    </div>
  );
}
