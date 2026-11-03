import { IconButton } from '@/components/ui';
import { Plus } from 'lucide-react';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

interface WorkspaceActionButtonProps {
  /** 发起协作（创建协作群），沿用现有 CreateGroupModal 链路。 */
  onCreateGroup: () => void;
  className?: string;
}

/** 协作群搜索工具行「发起协作」按钮（dmore artboard-010 x=251）：32px 白底描边方块、灰 + 图标，与搜索框同一视觉语言。 */
export function WorkspaceActionButton({ onCreateGroup, className }: WorkspaceActionButtonProps) {
  return (
    <IconButton
      label="发起协作"
      icon={<Plus className="h-[18px] w-[18px]" />}
      variant="ghost"
      className={`h-8 w-8 shrink-0 rounded-lg border border-input bg-background text-muted-foreground hover:bg-muted hover:text-foreground ${
        className ?? ''
      }`}
      onClick={() => {
        Tracert.click(Points.workspaceCollab.createGroup);
        onCreateGroup();
      }}
    />
  );
}