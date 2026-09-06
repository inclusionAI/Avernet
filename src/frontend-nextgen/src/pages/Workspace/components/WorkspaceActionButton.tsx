import { IconButton } from '@/components/ui';
import { Plus } from 'lucide-react';

interface WorkspaceActionButtonProps {
  /** 发起协作（创建协作群），沿用现有 CreateGroupModal 链路。 */
  onCreateGroup: () => void;
  className?: string;
}

/** 协作群搜索工具行「发起协作」按钮。 */
export function WorkspaceActionButton({ onCreateGroup, className }: WorkspaceActionButtonProps) {
  return (
    <IconButton
      label="发起协作"
      icon={<Plus className="h-4 w-4" />}
      variant="ghost"
      className={`h-9 w-9 rounded-md border border-primary/20 bg-primary/5 text-primary hover:border-primary/30 hover:bg-primary/10 hover:text-primary ${
        className ?? ''
      }`}
      onClick={onCreateGroup}
    />
  );
}
