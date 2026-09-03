import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { TaskListItem } from '@/domain/tasks/models';
import { getUserTaskAcceptanceText, getUserTaskGoal } from '../userTaskUtils';

/** 用户任务卡片的「目标 / 验收标准」元信息：单行截断 + Tooltip 展示全文，仅在对应字段存在时渲染。 */
export function UserTaskGoalMeta({ record }: { record: TaskListItem }) {
  const goal = record.task_spec?.goal;
  return (
    <>
      {goal?.objective ? (
        <TooltipProvider delayDuration={300}>
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="line-clamp-1 cursor-default text-xs text-muted-foreground">
                目标：{getUserTaskGoal(record)}
              </div>
            </TooltipTrigger>
            <TooltipContent className="max-w-sm whitespace-normal break-words">
              {getUserTaskGoal(record)}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : null}
      {goal?.acceptances?.length ? (
        <TooltipProvider delayDuration={300}>
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="line-clamp-1 cursor-default text-xs text-muted-foreground">
                验收标准：{getUserTaskAcceptanceText(record)}
              </div>
            </TooltipTrigger>
            <TooltipContent className="max-w-sm whitespace-normal break-words">
              {getUserTaskAcceptanceText(record)}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : null}
    </>
  );
}
