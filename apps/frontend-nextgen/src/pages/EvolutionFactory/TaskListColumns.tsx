import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { DataTableColumn } from '@/components/ui/DataTable';
import { canCancelStep, canRetryStep, formatTime, isBotEvolutionTask, type Task } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import { useMemo } from 'react';
import { stepName, taskTimes } from './presentation';
import { Hint, StatusBadge, TypeTag } from './sharedPrimitives';

/** 与 Bot 管理操作列同规范的文字链接：primary 蓝字 13px，hover 10% 蓝底。 */
const linkClass = 'h-7 rounded-md px-1.5 text-[13px] font-normal text-primary hover:bg-primary/10 hover:text-primary';
const dangerLinkClass =
  'h-7 rounded-md px-1.5 text-[13px] font-normal text-destructive hover:bg-destructive/10 hover:text-destructive';

/** 当前节点：优先展示可停止的执行节点，退化到可重试/最后一个节点。 */
function stepNameOf(task: Task) {
  const retryTarget = task.steps?.filter((candidate) => canRetryStep(task, candidate)).at(-1);
  const cancelTarget = task.steps?.find((candidate) => canCancelStep(task, candidate));
  const step = cancelTarget ?? retryTarget ?? task.steps?.at(-1);
  return step ? stepName(step.stepType) : '—';
}

export const COLUMN_WIDTHS = [
  'w-[clamp(220px,18vw,280px)]',
  'w-[clamp(140px,12vw,240px)]',
  'w-[clamp(100px,8vw,120px)]',
  'w-[clamp(150px,12vw,180px)]',
  'w-[clamp(96px,8vw,112px)]',
  'w-[clamp(84px,7vw,100px)]',
  'w-[clamp(160px,13vw,190px)]',
  'w-[clamp(104px,9vw,130px)]',
  'w-[200px]',
];

/** 进化任务列表列定义。busy 为当前操作中的任务 id，用于禁用重试/停止入口。 */
export function useTaskListColumns(
  busy: string,
  action: (taskId: string, stepId: string, operation: 'retry' | 'cancel') => Promise<void>,
) {
  return useMemo<DataTableColumn<Task>[]>(
    () => [
      {
        id: 'name',
        header: '任务名称',
        width: COLUMN_WIDTHS[0],
        cell: (task) => (
          <div className="min-w-0">
            <Hint content={task.task_name}>
              <span className="block truncate text-sm font-medium text-foreground">{task.task_name}</span>
            </Hint>
            <Hint content={task.task_id}>
              <span className="mt-0.5 block truncate font-mono text-xs text-content-icon">{task.task_id}</span>
            </Hint>
          </div>
        ),
      },
      {
        id: 'remark',
        header: '备注',
        width: COLUMN_WIDTHS[1],
        cell: (task) => (
          <Hint content={task.remark || undefined}>
            <span className="block truncate text-xs text-muted-foreground">{task.remark || '—'}</span>
          </Hint>
        ),
      },
      {
        id: 'type',
        header: '任务类型',
        width: COLUMN_WIDTHS[2],
        cell: (task) => <TypeTag type={task.task_type} />,
      },
      {
        id: 'object',
        header: '进化对象',
        width: COLUMN_WIDTHS[3],
        cell: (task) => (
          <div className="min-w-0">
            <Hint content={task.bot_id}>
              <span className="block truncate text-[13px] text-foreground">{task.bot_id}</span>
            </Hint>
            <span className="mt-0.5 block truncate text-xs text-content-icon">Owner {task.user_id}</span>
          </div>
        ),
      },
      {
        id: 'status',
        header: '状态',
        width: COLUMN_WIDTHS[4],
        cell: (task) => <StatusBadge status={task.status} dot />,
      },
      {
        id: 'creator',
        header: '创建人',
        width: COLUMN_WIDTHS[5],
        cell: (task) => <span className="text-[13px] text-foreground">{task.created_by}</span>,
      },
      {
        id: 'times',
        header: '开始/结束时间',
        width: COLUMN_WIDTHS[6],
        cell: (task) => {
          const times = taskTimes(task);
          return (
            <span className="block text-xs leading-5 text-muted-foreground">
              <span className="block">{formatTime(times.start)}</span>
              <span className="block">{formatTime(times.end)}</span>
            </span>
          );
        },
      },
      {
        id: 'node',
        header: '当前节点',
        width: COLUMN_WIDTHS[7],
        cell: (task) => <span className="text-[13px] text-foreground">{stepNameOf(task)}</span>,
      },
      {
        id: 'actions',
        header: '操作',
        width: COLUMN_WIDTHS[8],
        align: 'end',
        className: 'sticky right-0 z-10 bg-card group-hover:bg-muted/40',
        headerClassName: 'sticky right-0 z-10 bg-muted/50',
        cell: (task) => {
          const retryTarget = task.steps?.filter((candidate) => canRetryStep(task, candidate)).at(-1);
          const cancelTarget = task.steps?.find((candidate) => canCancelStep(task, candidate));
          const manage = isBotEvolutionTask(task);
          return (
            <div className="flex items-center justify-end gap-0.5">
              <Hint content={isBotEvolutionTask(task) ? undefined : '该历史任务不在本期迁移范围，请使用原入口'}>
                <Button
                  type="button"
                  variant="ghost"
                  className={linkClass}
                  disabled={!isBotEvolutionTask(task)}
                  onClick={() => history.push(`/evolution-factory/tasks/${encodeURIComponent(task.task_id)}`)}
                >
                  详情
                </Button>
              </Hint>
              {retryTarget && manage ? (
                <Button
                  type="button"
                  variant="ghost"
                  className={linkClass}
                  disabled={Boolean(busy)}
                  onClick={() => void action(task.task_id, retryTarget.stepId, 'retry').catch(() => undefined)}
                >
                  重试
                </Button>
              ) : null}
              {cancelTarget && manage ? (
                <ConfirmDialog
                  title="停止当前步骤？"
                  description="取消当前 Step；已完成步骤和产物保留。"
                  onConfirm={() => action(task.task_id, cancelTarget.stepId, 'cancel')}
                >
                  <Button type="button" variant="ghost" className={dangerLinkClass} disabled={Boolean(busy)}>
                    停止
                  </Button>
                </ConfirmDialog>
              ) : null}
            </div>
          );
        },
      },
    ],
    [busy],
  );
}