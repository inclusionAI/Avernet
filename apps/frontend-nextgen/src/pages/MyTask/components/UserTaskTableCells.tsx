import { Avatar } from '@/components/ui/Avatar';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { resolveBotAvatarUrl } from '@/domain/botAvatar';
import type { TaskListItem } from '@/domain/tasks/models';
import { formatDateTime, formatDuration } from '../userTaskUtils';
import { UserTaskGoalMeta } from './UserTaskGoalMeta';

export function UserTaskPrimaryContent({ record, title, lab }: { record: TaskListItem; title: string; lab: boolean }) {
  if (lab) {
    return (
      <div className="space-y-1">
        <TooltipProvider delayDuration={300}>
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0} className="block w-full truncate text-sm font-medium leading-5 text-foreground">
                {title}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-sm whitespace-normal break-words">{title}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <div className="w-full truncate font-mono text-xs leading-5 text-content-icon">{record.task_id}</div>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <TooltipProvider delayDuration={300}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="block max-w-[20rem] truncate font-medium leading-5 text-foreground">{title}</span>
          </TooltipTrigger>
          <TooltipContent>
            <div className="space-y-1">
              <div>{title}</div>
              <div className="font-mono text-muted-foreground">{record.task_id}</div>
            </div>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <UserTaskGoalMeta record={record} />
    </div>
  );
}

export function UserTaskGoalContent({ goal }: { goal: string }) {
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div tabIndex={0} className="w-full truncate text-[13px] leading-5 text-foreground">
            {goal}
          </div>
        </TooltipTrigger>
        <TooltipContent className="max-w-sm whitespace-normal break-words">{goal}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function UserTaskModifiedTime({ record, lab }: { record: TaskListItem; lab: boolean }) {
  return (
    <div
      className={
        lab ? 'whitespace-nowrap text-[13px] leading-5 text-foreground' : 'space-y-1 text-xs leading-5 text-foreground'
      }
    >
      <div>{formatDateTime(record.gmt_modified)}</div>
      {!lab ? (
        <div className="text-xs text-muted-foreground">
          耗时：{formatDuration(record.gmt_create, record.gmt_modified)}
        </div>
      ) : null}
    </div>
  );
}

export function UserTaskOwnerBotCell({ name, botId, avatarUrl }: { name: string; botId: string; avatarUrl?: string }) {
  return (
    <div className="flex items-center gap-2">
      <Avatar name={name} src={resolveBotAvatarUrl(avatarUrl, botId)} size={32} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium leading-5 text-foreground">{name}</div>
        <div className="truncate text-xs leading-5 text-muted-foreground">{botId}</div>
      </div>
    </div>
  );
}
