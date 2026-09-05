import { Button, IconButton } from '@/components/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { Plus, UserPlus, Users } from 'lucide-react';
import { useState } from 'react';

interface WorkspaceActionButtonProps {
  /** 添加好友，打开 Bot 广场弹窗。 */
  onAddFriend: () => void;
  /** 发起协作（创建协作群），沿用现有 CreateGroupModal 链路。 */
  onCreateGroup: () => void;
  className?: string;
}

/**
 * 工作台顶部「+」按钮：点击展开下拉菜单，提供「添加好友」「发起协作」两个入口。
 * 协作群 / 会话 两个侧栏共用，保证切换视图时按钮始终可见。
 */
export function WorkspaceActionButton({ onAddFriend, onCreateGroup, className }: WorkspaceActionButtonProps) {
  const [open, setOpen] = useState(false);

  const handleAddFriend = () => {
    setOpen(false);
    onAddFriend();
  };
  const handleCreateGroup = () => {
    setOpen(false);
    onCreateGroup();
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <IconButton
          label="添加好友或发起协作"
          icon={<Plus className="h-4 w-4" />}
          variant="ghost"
          className={`h-9 w-9 rounded-md border border-primary/20 bg-primary/5 text-primary hover:border-primary/30 hover:bg-primary/10 hover:text-primary ${className ?? ''}`}
        />
      </PopoverTrigger>
      <PopoverContent align="end" className="w-44 p-1">
        <Button
          variant="ghost"
          className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
          onClick={handleAddFriend}
        >
          <UserPlus className="h-3.5 w-3.5" />
          添加好友
        </Button>
        <Button
          variant="ghost"
          className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
          onClick={handleCreateGroup}
        >
          <Users className="h-3.5 w-3.5" />
          发起协作
        </Button>
      </PopoverContent>
    </Popover>
  );
}
