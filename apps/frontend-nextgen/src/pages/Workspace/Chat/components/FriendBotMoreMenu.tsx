// 好友 Bot 行的「更多操作」菜单(用户身份视角,PRD v1.5 AC-01)。
// 仅好友 Bot 分组渲染;菜单内提供红色警示项「移除好友」,由装配层弹出二次确认。
import { Button, IconButton, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { cn } from '@/utils/cn';
import { MoreHorizontal, UserMinus } from 'lucide-react';
import { useState } from 'react';

export interface FriendBotMoreMenuProps {
  botName: string;
  /** 触发器显隐类(与行内其它操作共用 hover/聚焦/菜单打开语义)。 */
  triggerClassName?: string;
  onRemoveFriend(): void;
}

export function FriendBotMoreMenu({ botName, triggerClassName, onRemoveFriend }: FriendBotMoreMenuProps) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <IconButton
          label="更多操作"
          ariaLabel="更多操作"
          size="sm"
          icon={<MoreHorizontal className="h-3.5 w-3.5" />}
          className={cn('h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground', triggerClassName)}
        />
      </PopoverTrigger>
      <PopoverContent align="end" className="w-44 p-1" onClick={(event) => event.stopPropagation()}>
        <Button
          variant="ghost"
          className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs text-destructive hover:text-destructive"
          aria-label={`移除好友 ${botName}`}
          onClick={() => {
            setOpen(false);
            onRemoveFriend();
          }}
        >
          <UserMinus className="h-3.5 w-3.5" />
          移除好友
        </Button>
      </PopoverContent>
    </Popover>
  );
}