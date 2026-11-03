import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { TaskListItem } from '@/domain/tasks/models';
import { UserTaskGoalMeta } from './UserTaskGoalMeta';

export function UserTaskPrimaryContent({ record, title, lab }: { record: TaskListItem; title: string; lab: boolean }) {
  if (lab) {
    return (
      <div className="space-y-1">
        <div className="max-w-[20rem] truncate font-mono text-[11px] leading-5 text-muted-foreground">
          {record.task_id}
        </div>
        <TooltipProvider delayDuration={300}>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="block max-w-[20rem] truncate font-medium leading-5 text-foreground">{title}</span>
            </TooltipTrigger>
            <TooltipContent className="max-w-sm whitespace-normal break-words">{title}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
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
          <div className="line-clamp-2 leading-5 text-foreground">{goal}</div>
        </TooltipTrigger>
        <TooltipContent className="max-w-sm whitespace-normal break-words">{goal}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
